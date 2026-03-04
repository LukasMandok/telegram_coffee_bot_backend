from __future__ import annotations

import asyncio

from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, ParamSpec, Sequence, TypeVar
from uuid import uuid4

from bson import BSON, ObjectId
from pymongo.errors import DocumentTooLarge

from ..common.log import Logger


log = Logger("SnapshotManager")

P = ParamSpec("P")
R = TypeVar("R")


DEFAULT_SNAPSHOT_COLLECTIONS: tuple[str, ...] = (
    "coffee_cards",
    "coffee_orders",
    "user_debts",
    "payments",
    "coffee_sessions",
)


@dataclass(frozen=True)
class SnapshotCollectionInfo:
    source_collection: str
    chunk_ids: tuple[ObjectId, ...]
    document_count: int


@dataclass(frozen=True)
class CapturedSnapshot:
    snapshot_id: str
    created_at: datetime
    reason: str
    collections: tuple[str, ...]
    documents_by_collection: Dict[str, List[Mapping[str, Any]]]


class SnapshotManager:
    """In-Mongo logical snapshots (no external dump files).

    Key behavior (per your requirements):
    - Links are NOT resolved; raw Mongo documents are stored.
    - For each source collection, we try to pack all docs into a single snapshot document.
      If that exceeds Mongo's doc limit, we automatically chunk.
    - Snapshot capture (reading source collections) is awaited (blocking).
    - Snapshot persist (writing snapshots_meta/snapshots_data) can be done in the background.
    """

    META_COLLECTION = "snapshots_meta"
    DATA_COLLECTION = "snapshots_data"

    # Leave headroom under MongoDB 16MB document limit.
    DEFAULT_MAX_BSON_BYTES = 15 * 1024 * 1024

    def __init__(
        self,
        db: Any,
        *,
        max_bson_bytes: int = DEFAULT_MAX_BSON_BYTES,
        logger: Logger | None = None,
    ) -> None:
        self.db = db
        self.max_bson_bytes = int(max_bson_bytes)
        self.logger = logger or log
        self.logger.info(
            f"SnapshotManager initialized: db_id={id(self.db)}, max_bson_bytes={self.max_bson_bytes}",
            extra_tag="SNAPSHOT",
        )

    def _meta(self):
        return self.db.get_collection(self.META_COLLECTION)

    def _data(self):
        return self.db.get_collection(self.DATA_COLLECTION)

    async def list_snapshots(self, *, include_pending: bool = False, limit: int = 50) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {}
        if not include_pending:
            query["status"] = "committed"

        cursor = self._meta().find(query).sort("created_at", -1).limit(int(limit))
        return [doc async for doc in cursor]

    async def get_snapshot_meta(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        return await self._meta().find_one({"snapshot_id": snapshot_id})

    async def capture_snapshot(
        self,
        *,
        snapshot_id: str,
        reason: str,
        collections: Sequence[str] = DEFAULT_SNAPSHOT_COLLECTIONS,
    ) -> CapturedSnapshot:
        self.logger.debug(
            f"Starting snapshot capture: snapshot_id={snapshot_id}, reason={reason}, collections={list(collections)}",
            extra_tag="SNAPSHOT",
        )
        created_at = datetime.now()
        docs_by_collection: Dict[str, List[Mapping[str, Any]]] = {}

        for collection_name in collections:
            source = self.db.get_collection(collection_name)
            docs: List[Mapping[str, Any]] = []
            async for doc in source.find({}):
                docs.append(doc)
            docs_by_collection[collection_name] = docs
            self.logger.trace(
                f"Captured collection: snapshot_id={snapshot_id}, collection={collection_name}, documents={len(docs)}",
                extra_tag="SNAPSHOT",
            )

        total_docs = sum(len(docs) for docs in docs_by_collection.values())
        self.logger.info(
            f"Snapshot capture completed: snapshot_id={snapshot_id}, total_documents={total_docs}",
            extra_tag="SNAPSHOT",
        )

        return CapturedSnapshot(
            snapshot_id=snapshot_id,
            created_at=created_at,
            reason=reason,
            collections=tuple(collections),
            documents_by_collection=docs_by_collection,
        )

    async def create_snapshot(
        self,
        *,
        reason: str,
        collections: Sequence[str] = DEFAULT_SNAPSHOT_COLLECTIONS,
        keep_last: int = 10,
        persist_in_background: bool = True,
    ) -> str:
        snapshot_id = uuid4().hex
        self.logger.debug(
            f"create_snapshot called: snapshot_id={snapshot_id}, reason={reason}, background={persist_in_background}",
            extra_tag="SNAPSHOT",
        )
        captured = await self.capture_snapshot(snapshot_id=snapshot_id, reason=reason, collections=collections)
        await self._persist_captured_snapshot(captured, keep_last=keep_last, background=persist_in_background)
        return snapshot_id

    def pending_snapshot(
        self,
        *,
        reason: str,
        collections: Sequence[str] = DEFAULT_SNAPSHOT_COLLECTIONS,
        keep_last: int = 10,
        persist_in_background: bool = True,
    ) -> "PendingSnapshot":
        self.logger.trace(
            f"pending_snapshot created: reason={reason}, collections={list(collections)}, keep_last={keep_last}, background={persist_in_background}",
            extra_tag="SNAPSHOT",
        )
        return PendingSnapshot(
            self,
            reason=reason,
            collections=collections,
            keep_last=keep_last,
            persist_in_background=persist_in_background,
        )

    async def restore_snapshot(
        self,
        snapshot_id: str,
        *,
        collections: Sequence[str] | None = None,
    ) -> None:
        meta = await self.get_snapshot_meta(snapshot_id)
        if not meta:
            raise ValueError(f"Snapshot not found: {snapshot_id}")
        if meta.get("status") != "committed":
            raise ValueError(f"Snapshot is not committed: {snapshot_id}")

        available_collections = list((meta.get("collections") or {}).keys())
        target_collections = list(collections) if collections is not None else available_collections

        self.logger.info(
            f"Restoring snapshot {snapshot_id} for collections: {target_collections}",
            extra_tag="SNAPSHOT",
        )

        for collection_name in target_collections:
            info = (meta.get("collections") or {}).get(collection_name)
            if not info:
                continue

            chunk_ids: List[ObjectId] = list(info.get("chunk_ids") or [])
            docs = await self._load_snapshot_documents(snapshot_id, collection_name, chunk_ids)

            target = self.db.get_collection(collection_name)
            await target.delete_many({})
            if docs:
                await target.insert_many(docs, ordered=False)

        self.logger.info(f"Restored snapshot {snapshot_id}", extra_tag="SNAPSHOT")

    async def delete_snapshot(self, snapshot_id: str) -> bool:
        meta = await self.get_snapshot_meta(snapshot_id)
        if not meta:
            return False

        await self._delete_snapshot_by_meta(meta)
        return True

    async def _persist_captured_snapshot(
        self,
        captured: CapturedSnapshot,
        *,
        keep_last: int,
        background: bool,
    ) -> None:
        async def persist() -> None:
            meta_id: ObjectId | None = None
            inserted_chunk_ids: List[ObjectId] = []
            try:
                self.logger.debug(
                    f"Persist started: snapshot_id={captured.snapshot_id}, keep_last={keep_last}",
                    extra_tag="SNAPSHOT",
                )
                meta_doc: Dict[str, Any] = {
                    "snapshot_id": captured.snapshot_id,
                    "created_at": captured.created_at,
                    "reason": captured.reason,
                    "status": "writing",
                    "collections": {},
                    "total_documents": 0,
                }

                meta_insert = await self._meta().insert_one(meta_doc)
                meta_id = meta_insert.inserted_id

                total_docs = 0
                collections_info: Dict[str, Any] = {}

                for collection_name in captured.collections:
                    docs = captured.documents_by_collection.get(collection_name, [])
                    self.logger.trace(
                        f"Persisting collection: snapshot_id={captured.snapshot_id}, collection={collection_name}, documents={len(docs)}",
                        extra_tag="SNAPSHOT",
                    )
                    info = await self._persist_one_collection(
                        snapshot_id=captured.snapshot_id,
                        source_collection=collection_name,
                        documents=docs,
                    )
                    inserted_chunk_ids.extend(list(info.chunk_ids))
                    total_docs += info.document_count
                    collections_info[collection_name] = {
                        "chunk_ids": list(info.chunk_ids),
                        "document_count": info.document_count,
                    }

                await self._meta().update_one(
                    {"_id": meta_id},
                    {
                        "$set": {
                            "status": "committed",
                            "committed_at": datetime.now(),
                            "collections": collections_info,
                            "total_documents": total_docs,
                        }
                    },
                )

                self.logger.info(
                    f"Persist committed: snapshot_id={captured.snapshot_id}, total_documents={total_docs}, chunk_count={len(inserted_chunk_ids)}",
                    extra_tag="SNAPSHOT",
                )

                await self._prune_old_snapshots(keep_last=keep_last)

            except Exception as e:
                self.logger.error(
                    f"Snapshot persist failed: snapshot_id={captured.snapshot_id}, error={e}",
                    extra_tag="SNAPSHOT",
                    exc_info=e,
                )
                try:
                    if inserted_chunk_ids:
                        await self._data().delete_many({"_id": {"$in": inserted_chunk_ids}})
                    if meta_id is not None:
                        await self._meta().delete_one({"_id": meta_id})
                except Exception:
                    pass

        if background:
            self.logger.debug(
                f"Scheduling snapshot persist in background: snapshot_id={captured.snapshot_id}",
                extra_tag="SNAPSHOT",
            )
            asyncio.create_task(persist())
        else:
            self.logger.debug(
                f"Running snapshot persist inline: snapshot_id={captured.snapshot_id}",
                extra_tag="SNAPSHOT",
            )
            await persist()

    async def _persist_one_collection(
        self,
        *,
        snapshot_id: str,
        source_collection: str,
        documents: Sequence[Mapping[str, Any]],
    ) -> SnapshotCollectionInfo:
        # Try single packed doc first.
        try:
            chunk_id = await self._insert_data_chunk(
                snapshot_id=snapshot_id,
                source_collection=source_collection,
                chunk_index=0,
                documents=documents,
            )
            return SnapshotCollectionInfo(
                source_collection=source_collection,
                chunk_ids=(chunk_id,),
                document_count=len(documents),
            )
        except DocumentTooLarge:
            pass

        # Fall back to chunking by BSON size.
        chunk_ids: List[ObjectId] = []
        current: List[Mapping[str, Any]] = []
        current_bytes = 0
        chunk_index = 0

        for doc in documents:
            doc_bytes = len(BSON.encode(dict(doc)))
            if current and (current_bytes + doc_bytes) > self.max_bson_bytes:
                inserted = await self._insert_data_chunk_with_split(
                    snapshot_id=snapshot_id,
                    source_collection=source_collection,
                    chunk_index=chunk_index,
                    documents=current,
                )
                chunk_ids.extend(inserted)
                chunk_index += len(inserted)
                current = []
                current_bytes = 0

            current.append(doc)
            current_bytes += doc_bytes

        if current:
            inserted = await self._insert_data_chunk_with_split(
                snapshot_id=snapshot_id,
                source_collection=source_collection,
                chunk_index=chunk_index,
                documents=current,
            )
            chunk_ids.extend(inserted)

        return SnapshotCollectionInfo(
            source_collection=source_collection,
            chunk_ids=tuple(chunk_ids),
            document_count=len(documents),
        )

    async def _insert_data_chunk(
        self,
        *,
        snapshot_id: str,
        source_collection: str,
        chunk_index: int,
        documents: Sequence[Mapping[str, Any]],
    ) -> ObjectId:
        doc: Dict[str, Any] = {
            "snapshot_id": snapshot_id,
            "source_collection": source_collection,
            "chunk_index": int(chunk_index),
            "created_at": datetime.now(),
            "document_count": len(documents),
            "documents": [dict(d) for d in documents],
        }

        if len(BSON.encode(doc)) > self.max_bson_bytes:
            raise DocumentTooLarge("Snapshot chunk exceeds BSON size limit")

        result = await self._data().insert_one(doc)
        return result.inserted_id

    async def _insert_data_chunk_with_split(
        self,
        *,
        snapshot_id: str,
        source_collection: str,
        chunk_index: int,
        documents: Sequence[Mapping[str, Any]],
    ) -> List[ObjectId]:
        try:
            inserted_id = await self._insert_data_chunk(
                snapshot_id=snapshot_id,
                source_collection=source_collection,
                chunk_index=chunk_index,
                documents=documents,
            )
            return [inserted_id]
        except DocumentTooLarge:
            if len(documents) <= 1:
                raise

        self.logger.debug(
            f"Chunk split required: snapshot_id={snapshot_id}, collection={source_collection}, chunk_index={chunk_index}, documents={len(documents)}",
            extra_tag="SNAPSHOT",
        )

        mid = len(documents) // 2
        left = await self._insert_data_chunk_with_split(
            snapshot_id=snapshot_id,
            source_collection=source_collection,
            chunk_index=chunk_index,
            documents=documents[:mid],
        )
        right = await self._insert_data_chunk_with_split(
            snapshot_id=snapshot_id,
            source_collection=source_collection,
            chunk_index=chunk_index + len(left),
            documents=documents[mid:],
        )
        return left + right

    async def _load_snapshot_documents(
        self,
        snapshot_id: str,
        source_collection: str,
        chunk_ids: Sequence[ObjectId],
    ) -> List[Mapping[str, Any]]:
        if not chunk_ids:
            return []

        cursor = (
            self._data()
            .find(
                {
                    "snapshot_id": snapshot_id,
                    "source_collection": source_collection,
                    "_id": {"$in": list(chunk_ids)},
                }
            )
            .sort("chunk_index", 1)
        )

        docs: List[Mapping[str, Any]] = []
        async for chunk in cursor:
            chunk_docs = chunk.get("documents") or []
            docs.extend(chunk_docs)

        self.logger.trace(
            f"Loaded snapshot documents: snapshot_id={snapshot_id}, collection={source_collection}, chunks={len(chunk_ids)}, documents={len(docs)}",
            extra_tag="SNAPSHOT",
        )
        return docs

    async def _delete_snapshot_by_meta(self, meta: Mapping[str, Any]) -> None:
        collections = meta.get("collections") or {}
        ids: List[ObjectId] = []
        for _, info in collections.items():
            ids.extend(list(info.get("chunk_ids") or []))

        if ids:
            await self._data().delete_many({"_id": {"$in": ids}})
        await self._meta().delete_one({"_id": meta["_id"]})

    async def _prune_old_snapshots(self, *, keep_last: int) -> None:
        keep_last = int(keep_last)
        if keep_last <= 0:
            return

        cursor = self._meta().find({"status": "committed"}).sort("created_at", -1).skip(keep_last)
        to_delete = [doc async for doc in cursor]
        for meta in to_delete:
            await self._delete_snapshot_by_meta(meta)


class PendingSnapshot:
    def __init__(
        self,
        manager: SnapshotManager,
        *,
        reason: str,
        collections: Sequence[str],
        keep_last: int,
        persist_in_background: bool,
    ) -> None:
        self.manager = manager
        self.reason = reason
        self.collections = collections
        self.keep_last = int(keep_last)
        self.persist_in_background = bool(persist_in_background)

        self.snapshot_id: str | None = None
        self._captured: CapturedSnapshot | None = None
        self._aborted = False

    async def __aenter__(self) -> "PendingSnapshot":
        self.snapshot_id = uuid4().hex
        self.manager.logger.debug(
            f"PendingSnapshot enter: snapshot_id={self.snapshot_id}, reason={self.reason}",
            extra_tag="SNAPSHOT",
        )
        self._captured = await self.manager.capture_snapshot(
            snapshot_id=self.snapshot_id,
            reason=self.reason,
            collections=self.collections,
        )
        return self

    async def abort(self) -> None:
        """Prevent commit on __aexit__ and drop captured state."""
        self._aborted = True
        self._captured = None
        self.manager.logger.info(
            f"PendingSnapshot aborted: snapshot_id={self.snapshot_id}",
            extra_tag="SNAPSHOT",
        )

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None or self._aborted:
            self.manager.logger.debug(
                f"PendingSnapshot exit without commit: snapshot_id={self.snapshot_id}, exc_type={exc_type}, aborted={self._aborted}",
                extra_tag="SNAPSHOT",
            )
            self._captured = None
            return False

        if not self._captured:
            return False

        await self.manager._persist_captured_snapshot(
            self._captured,
            keep_last=self.keep_last,
            background=self.persist_in_background,
        )
        self.manager.logger.info(
            f"PendingSnapshot committed: snapshot_id={self.snapshot_id}, background={self.persist_in_background}",
            extra_tag="SNAPSHOT",
        )
        self._captured = None
        return False


def pending_snapshot(
    reason: str | Callable[..., str],
    *,
    snapshot_manager_getter: Callable[[Any], Any] = lambda self: self.api.get_snapshot_manager(),
    enabled: Callable[..., bool] | None = None,
    inject_snapshot_kwarg: str | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Decorator that wraps an async method in SnapshotManager.pending_snapshot(...).

    - `reason`: string or a callable that computes a reason from `*args, **kwargs`
    - `enabled`: optional callable; when it returns False, no snapshot context is used
    - `inject_snapshot_kwarg`: if set, injects the yielded snapshot object as kwarg
    """

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            if enabled is not None and not enabled(*args, **kwargs):
                return await func(*args, **kwargs)

            self = args[0]
            snapshot_manager = snapshot_manager_getter(self)
            computed_reason = reason(*args, **kwargs) if callable(reason) else reason
            snapshot_manager.logger.trace(
                f"pending_snapshot decorator activated: reason={computed_reason}, function={func.__name__}",
                extra_tag="SNAPSHOT",
            )

            async with snapshot_manager.pending_snapshot(reason=computed_reason) as snapshot:
                if inject_snapshot_kwarg is not None and inject_snapshot_kwarg not in kwargs:
                    kwargs[inject_snapshot_kwarg] = snapshot  # type: ignore[assignment]

                return await func(*args, **kwargs)

        return wrapper

    return decorator
