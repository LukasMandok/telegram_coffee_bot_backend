from __future__ import annotations

import asyncio
from contextvars import ContextVar

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

# NOTE: I am not sure, if this is actually the best idea to do....

_CURRENT_PENDING_SNAPSHOT: ContextVar[Any | None] = ContextVar("current_pending_snapshot", default=None)


def get_current_pending_snapshot() -> Any | None:
    """Return the current PendingSnapshot (if any) for this async context."""
    return _CURRENT_PENDING_SNAPSHOT.get()


def _append_to_pending_snapshot(*, reason: str, context: str | None) -> "PendingSnapshot | None":
    current = get_current_pending_snapshot()
    if isinstance(current, PendingSnapshot) and current.is_active:
        try:
            current.add_reason(reason)
            if context is not None:
                current.add_context(context)
        except Exception:
            pass
        return current
    return None


DEFAULT_SNAPSHOT_COLLECTIONS: tuple[str, ...] = (
    "coffee_cards",
    "coffee_orders",
    "user_debts",
    "payments",
    "coffee_sessions",
)


DEFAULT_SNAPSHOT_SETTINGS: Dict[str, Any] = {
    "keep_last": 10,
    "card_closed": True,
    "session_completed": True,
    "quick_order": False,
    "card_created": True,
}


@dataclass(frozen=True)
class SnapshotCollectionInfo:
    source_collection: str
    chunk_ids: tuple[ObjectId, ...]
    document_count: int


@dataclass
class Snapshot:
    snapshot_id: str
    created_at: datetime
    reasons: List[str]
    contexts: List[str]
    collections: tuple[str, ...]
    documents_by_collection: Dict[str, List[Mapping[str, Any]]]

    def add_reason(self, reason: str) -> None:
        self.reasons.append(reason)

    def add_context(self, context: str) -> None:
        self.contexts.append(context)

    def print_reason(self) -> str:
        return ", ".join(self.reasons)

    def print_context(self) -> str:
        return " | ".join(self.contexts)


class SnapshotManager:
    """In-Mongo logical snapshots (no external dump files).

    Key behavior (per your requirements):
    - Links are NOT resolved; raw Mongo documents are stored.
    - For each source collection, we try to pack all docs into a single snapshot document.
      If that exceeds Mongo's doc limit, we automatically chunk.
    - Snapshot capture (reading source collections) is awaited (blocking).
    - Snapshot saving (writing snapshots_meta/snapshots_data) can be done in the background.
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

        self._snapshot_settings: Dict[str, Any] = dict(DEFAULT_SNAPSHOT_SETTINGS)

        self.logger.info(
            f"SnapshotManager initialized: db_id={id(self.db)}, max_bson_bytes={self.max_bson_bytes}",
            extra_tag="SNAPSHOT",
        )

    def _meta_collection(self):
        return self.db.get_collection(self.META_COLLECTION)

    def _data_collection(self):
        return self.db.get_collection(self.DATA_COLLECTION)

    async def _refresh_snapshot_settings(self) -> None:
        """Refresh snapshot settings from AppSettings (raw Mongo doc).

        SnapshotManager is intentionally independent from Beanie/Pydantic models.
        This is called before snapshot operations to keep behavior consistent
        with admin-configured values.
        """
        # NOTE: check if this merging logic is really necessary
        try:
            doc = await self.db.get_collection("app_settings").find_one({})
            snapshot_settings = (doc or {}).get("snapshots")

            merged: Dict[str, Any] = dict(DEFAULT_SNAPSHOT_SETTINGS)
            if isinstance(snapshot_settings, dict):
                merged.update({k: v for k, v in snapshot_settings.items() if v is not None})

            self._snapshot_settings = merged
        except Exception as exc:
            self.logger.debug(
                f"Failed to refresh snapshot settings; keeping cached: {type(exc).__name__}: {exc!r}",
                extra_tag="SNAPSHOT",
            )

    def _get_snapshot_setting(self, key: str, default: Any) -> Any:
        return self._snapshot_settings.get(key, default)

    def _is_context_snapshot_active(self, context: str | None) -> bool:
        """Return True if a snapshot should be created for this context.

        Supports optional suffixes like `card_closed:<card_id>` by only
        considering the prefix before the first `:`.

        Unknown/None contexts are treated as active (not gated by settings).
        """
        if not context:
            return True

        key = context.split(":", 1)[0]
        # TODO: make this not hardcoded
        if key not in {"card_closed", "session_completed", "quick_order", "card_created"}:
            return True

        return bool(self._get_snapshot_setting(key, True))

    async def list_snapshots(self, *, include_pending: bool = False, limit: int = 50) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {}
        if not include_pending:
            query["status"] = "committed"

        cursor = self._meta_collection().find(query).sort("created_at", -1).limit(int(limit))
        return [doc async for doc in cursor]

    async def get_last_loaded_snapshot_meta(self) -> Optional[Dict[str, Any]]:
        """Return the most recently loaded (restored) snapshot meta, if any.

        We track loads by writing `loaded_at` (and optional `loaded_by_user_id`) onto
        the snapshot's meta document.
        """
        cursor = (
            self._meta_collection()
            .find({"loaded_at": {"$exists": True}})
            .sort("loaded_at", -1)
            .limit(1)
        )
        docs = [doc async for doc in cursor]
        return docs[0] if docs else None

    # Note: this can mabye be simplified 

    async def mark_snapshot_loaded(self, snapshot_id: str, *, loaded_by_user_id: int | None = None) -> None:
        """Mark a snapshot as loaded (restored) and update obsolete markers.

        Rules:
        - When restoring snapshot X at time T, all snapshots with
          `created_at > X.created_at` and `created_at <= T` become `obsolete=True`.
          (They represent states that are newer than the restored DB state.)
        - If X was itself obsolete, we "rewind" that obsolete window by clearing
          obsolete markers for snapshots below X until the previously loaded snapshot.
        """
        if not snapshot_id:
            return

        snapshot_id = str(snapshot_id)
        now = datetime.now()

        meta = await self.get_snapshot_meta(snapshot_id)
        if not meta:
            return

        target_created_at = meta.get("created_at")
        if not isinstance(target_created_at, datetime):
            return

        target_was_obsolete = bool(meta.get("obsolete"))

        # The snapshot that was loaded before this restore (used for clearing when
        # restoring an obsolete snapshot).
        previous_loaded: Optional[Dict[str, Any]] = None
        try:
            cursor = (
                self._meta_collection()
                .find({"loaded_at": {"$exists": True}, "snapshot_id": {"$ne": snapshot_id}})
                .sort("loaded_at", -1)
                .limit(1)
            )
            docs = [doc async for doc in cursor]
            previous_loaded = docs[0] if docs else None
        except Exception:
            previous_loaded = None

        payload: Dict[str, Any] = {"loaded_at": now}
        if loaded_by_user_id is not None:
            payload["loaded_by_user_id"] = int(loaded_by_user_id)

        if target_was_obsolete:
            await self._meta_collection().update_one(
                {"snapshot_id": snapshot_id},
                {
                    "$set": {**payload, "obsolete": False},
                    "$unset": {"obsoleted_at": "", "obsoleted_by_snapshot_id": ""},
                },
            )
        else:
            await self._meta_collection().update_one(
                {"snapshot_id": snapshot_id},
                {"$set": payload},
            )

        # Mark snapshots created after the restored snapshot (until now) as obsolete.
        await self._meta_collection().update_many(
            {
                "status": "committed",
                "snapshot_id": {"$ne": snapshot_id},
                "created_at": {"$gt": target_created_at, "$lte": now},
            },
            {
                "$set": {
                    "obsolete": True,
                    "obsoleted_at": now,
                    "obsoleted_by_snapshot_id": snapshot_id,
                }
            },
        )

        # If we restored an obsolete snapshot, clear obsolete markers for snapshots
        # below it until the previously loaded snapshot.
        if target_was_obsolete and previous_loaded is not None:
            prev_created_at = previous_loaded.get("created_at")
            if isinstance(prev_created_at, datetime):
                await self._meta_collection().update_many(
                    {
                        "status": "committed",
                        "created_at": {"$gt": prev_created_at, "$lte": target_created_at},
                        "obsolete": True,
                    },
                    {
                        "$set": {"obsolete": False},
                        "$unset": {"obsoleted_at": "", "obsoleted_by_snapshot_id": ""},
                    },
                )

    async def get_snapshot_meta(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        return await self._meta_collection().find_one({"snapshot_id": snapshot_id})

    async def capture_snapshot(
        self,
        *,
        snapshot_id: str,
        reason: str,
        context: str | None = None,
        collections: Sequence[str] = DEFAULT_SNAPSHOT_COLLECTIONS,
    ) -> Snapshot:
        self.logger.debug(
            f"Starting snapshot capture: snapshot_id={snapshot_id}, reason={reason}, context={context}, collections={list(collections)}",
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

        return Snapshot(
            snapshot_id=snapshot_id,
            created_at=created_at,
            reasons=[reason],
            contexts=[context] if context is not None else [],
            collections=tuple(collections),
            documents_by_collection=docs_by_collection,
        )

    async def create_snapshot(
        self,
        *,
        reason: str,
        context: str | None = None,
        collections: Sequence[str] = DEFAULT_SNAPSHOT_COLLECTIONS,
        save_in_background: bool = True,
    ) -> str:
        current = _append_to_pending_snapshot(reason=reason, context=context)
        if current is not None:
            return current.snapshot_id or ""

        await self._refresh_snapshot_settings()
        if not self._is_context_snapshot_active(context):
            self.logger.debug(
                f"create_snapshot skipped by settings: reason={reason}, context={context}",
                extra_tag="SNAPSHOT",
            )
            return ""

        snapshot_id = uuid4().hex

        self.logger.debug(
            f"create_snapshot called: snapshot_id={snapshot_id}, reason={reason}, context={context}, background={save_in_background}",
            extra_tag="SNAPSHOT",
        )
        snapshot = await self.capture_snapshot(
            snapshot_id=snapshot_id,
            reason=reason,
            context=context,
            collections=collections,
        )
        await self.save_snapshot(snapshot, save_in_background=save_in_background)
        return snapshot_id

    def pending_snapshot(
        self,
        *,
        reason: str,
        context: str | None = None,
        collections: Sequence[str] = DEFAULT_SNAPSHOT_COLLECTIONS,
        save_in_background: bool = True,
    ) -> "PendingSnapshot":
        self.logger.trace(
            f"pending_snapshot created: reason={reason}, context={context}, collections={list(collections)}, background={save_in_background}",
            extra_tag="SNAPSHOT",
        )
        return PendingSnapshot(
            self,
            reason=reason,
            context=context,
            collections=collections,
            save_in_background=save_in_background,
        )

    async def save_snapshot(self, snapshot: Snapshot, *, save_in_background: bool) -> None:
        """Saves a captured snapshot and prune old snapshots based on current settings."""

        async def save() -> None:
            await self._refresh_snapshot_settings()
            keep_last = int(self._get_snapshot_setting("keep_last", DEFAULT_SNAPSHOT_SETTINGS["keep_last"]))

            meta_id: ObjectId | None = None
            inserted_chunk_ids: List[ObjectId] = []
            try:
                self.logger.debug(
                    f"Save started: snapshot_id={snapshot.snapshot_id}, keep_last={keep_last}",
                    extra_tag="SNAPSHOT",
                )

                meta_doc: Dict[str, Any] = {
                    "snapshot_id": snapshot.snapshot_id,
                    "created_at": snapshot.created_at,
                    "reasons": list(snapshot.reasons),
                    "contexts": list(snapshot.contexts),
                    "status": "writing",
                    "collections": {},
                    "total_documents": 0,
                }

                meta_insert = await self._meta_collection().insert_one(meta_doc)
                meta_id = meta_insert.inserted_id

                total_docs = 0
                collections_info: Dict[str, Any] = {}

                for collection_name in snapshot.collections:
                    docs = snapshot.documents_by_collection.get(collection_name, [])
                    self.logger.trace(
                        f"Saving collection: snapshot_id={snapshot.snapshot_id}, collection={collection_name}, documents={len(docs)}",
                        extra_tag="SNAPSHOT",
                    )
                    info = await self._save_one_collection(
                        snapshot_id=snapshot.snapshot_id,
                        source_collection=collection_name,
                        documents=docs,
                    )
                    inserted_chunk_ids.extend(list(info.chunk_ids))
                    total_docs += info.document_count
                    collections_info[collection_name] = {
                        "chunk_ids": list(info.chunk_ids),
                        "document_count": info.document_count,
                    }

                await self._meta_collection().update_one(
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
                    f"Save committed: snapshot_id={snapshot.snapshot_id}, total_documents={total_docs}, chunk_count={len(inserted_chunk_ids)}",
                    extra_tag="SNAPSHOT",
                )

                await self._prune_old_snapshots(keep_last=keep_last)

            except Exception as e:
                self.logger.error(
                    f"Snapshot save failed: snapshot_id={snapshot.snapshot_id}, error={e}",
                    extra_tag="SNAPSHOT",
                    exc_info=e,
                )
                try:
                    if inserted_chunk_ids:
                        await self._data_collection().delete_many({"_id": {"$in": inserted_chunk_ids}})
                    if meta_id is not None:
                        await self._meta_collection().delete_one({"_id": meta_id})
                except Exception:
                    pass

        if save_in_background:
            self.logger.debug(
                f"Scheduling snapshot save in background: snapshot_id={snapshot.snapshot_id}",
                extra_tag="SNAPSHOT",
            )
            asyncio.create_task(save())
        else:
            self.logger.debug(
                f"Running snapshot save inline: snapshot_id={snapshot.snapshot_id}",
                extra_tag="SNAPSHOT",
            )
            await save()

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

    async def clear_all_snapshots(self) -> Dict[str, int]:
        """Delete all snapshot metadata and snapshot data documents.

        This removes ALL documents from `snapshots_meta` and `snapshots_data`.
        Returns counts for UI reporting.
        """
        self.logger.warning("Clearing ALL snapshots (meta + data)", extra_tag="SNAPSHOT")

        deleted_data = await self._data_collection().delete_many({})
        deleted_meta = await self._meta_collection().delete_many({})

        result = {
            "deleted_meta": int(getattr(deleted_meta, "deleted_count", 0)),
            "deleted_data": int(getattr(deleted_data, "deleted_count", 0)),
        }
        self.logger.warning(f"Cleared snapshots: {result}", extra_tag="SNAPSHOT")
        return result

    async def _save_captured_snapshot(
        self,
        captured: Snapshot,
        *,
        save_in_background: bool,
    ) -> None:
        """Backward-compatible alias (older name)."""
        # keep_last is ignored; retention is controlled by current settings.
        await self.save_snapshot(captured, save_in_background=save_in_background)

    async def _save_one_collection(
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

        result = await self._data_collection().insert_one(doc)
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
            self._data_collection()
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
            await self._data_collection().delete_many({"_id": {"$in": ids}})
        await self._meta_collection().delete_one({"_id": meta["_id"]})

    async def _prune_old_snapshots(self, *, keep_last: int) -> None:
        keep_last = int(keep_last)
        if keep_last <= 0:
            return

        cursor = self._meta_collection().find({"status": "committed"}).sort("created_at", -1).skip(keep_last)
        to_delete = [doc async for doc in cursor]
        for meta in to_delete:
            await self._delete_snapshot_by_meta(meta)


class PendingSnapshot:
    def __init__(
        self,
        manager: SnapshotManager,
        *,
        reason: str,
        context: str | None,
        collections: Sequence[str],
        save_in_background: bool,
    ) -> None:
        self.manager = manager
        self.reason = reason
        self.context = context
        self.collections = collections
        self.save_in_background = bool(save_in_background)

        self.snapshot_id: str | None = None
        self._snapshot: Snapshot | None = None
        self._aborted = False
        self._token: Any | None = None
        self._nested_under: "PendingSnapshot | None" = None

    @property
    def is_active(self) -> bool:
        """True when this pending snapshot is tracking a captured Snapshot."""
        return self._snapshot is not None and not self._aborted

    def add_reason(self, reason: str) -> None:
        if self._snapshot is None:
            return
        self._snapshot.add_reason(reason)

    def add_context(self, context: str) -> None:
        if self._snapshot is None:
            return
        self._snapshot.add_context(context)

    async def __aenter__(self) -> "PendingSnapshot":
        parent = _append_to_pending_snapshot(reason=self.reason, context=self.context)
        if parent is not None:
            self._nested_under = parent
            self.snapshot_id = parent.snapshot_id
            return self

        await self.manager._refresh_snapshot_settings()
        if not self.manager._is_context_snapshot_active(self.context):
            self.manager.logger.debug(
                f"PendingSnapshot skipped by settings: context={self.context}",
                extra_tag="SNAPSHOT",
            )
            self._snapshot = None
            self.snapshot_id = None
            return self

        self.snapshot_id = uuid4().hex
        self.manager.logger.debug(
            f"PendingSnapshot enter: snapshot_id={self.snapshot_id}, reason={self.reason}, context={self.context}",
            extra_tag="SNAPSHOT",
        )

        self._token = _CURRENT_PENDING_SNAPSHOT.set(self)
        try:
            self._snapshot = await self.manager.capture_snapshot(
                snapshot_id=self.snapshot_id,
                reason=self.reason,
                context=self.context,
                collections=self.collections,
            )
        except Exception:
            if self._token is not None:
                _CURRENT_PENDING_SNAPSHOT.reset(self._token)
                self._token = None
            raise

        return self

    async def abort(self) -> None:
        """Prevent commit on __aexit__ and drop captured state."""
        self._aborted = True
        self._snapshot = None
        self.manager.logger.info(
            f"PendingSnapshot aborted: snapshot_id={self.snapshot_id}",
            extra_tag="SNAPSHOT",
        )

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if self._nested_under is not None:
            return False

        if exc_type is not None or self._aborted:
            self.manager.logger.debug(
                f"PendingSnapshot exit without commit: snapshot_id={self.snapshot_id}, exc_type={exc_type}, aborted={self._aborted}",
                extra_tag="SNAPSHOT",
            )
            self._snapshot = None
            if self._token is not None:
                _CURRENT_PENDING_SNAPSHOT.reset(self._token)
                self._token = None
            return False

        if not self._snapshot:
            return False

        await self.manager.save_snapshot(self._snapshot, save_in_background=self.save_in_background)
        self.manager.logger.info(
            f"PendingSnapshot committed: snapshot_id={self.snapshot_id}, background={self.save_in_background}",
            extra_tag="SNAPSHOT",
        )
        self._snapshot = None
        if self._token is not None:
            _CURRENT_PENDING_SNAPSHOT.reset(self._token)
            self._token = None
        return False


def pending_snapshot(
    context: str | Callable[..., str],
    *,
    reason: str | Callable[..., str] | None = None,
    snapshot_manager_getter: Callable[[Any], Any] = lambda self: self.api.get_snapshot_manager()
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Decorator that wraps an async method in SnapshotManager.pending_snapshot(...).

    - `context`: string or a callable that computes a context from `*args, **kwargs`
    - `reason`: optional string/callable for a human-readable reason; defaults to `context`
    - `enabled`: optional callable; when it returns False, no snapshot context is used
    - `inject_snapshot_kwarg`: if set, injects the yielded snapshot object as kwarg
    """

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            self = args[0]
            snapshot_manager = snapshot_manager_getter(self)
            computed_context = context(*args, **kwargs) if callable(context) else context
            computed_reason = (
                (reason(*args, **kwargs) if callable(reason) else reason)
                if reason is not None
                else computed_context
            )
            snapshot_manager.logger.trace(
                f"pending_snapshot decorator activated: reason={computed_reason}, context={computed_context}, function={func.__name__}",
                extra_tag="SNAPSHOT",
            )

            async with snapshot_manager.pending_snapshot(reason=computed_reason, context=computed_context):
                return await func(*args, **kwargs)

        return wrapper

    return decorator
