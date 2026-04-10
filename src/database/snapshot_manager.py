from __future__ import annotations

import asyncio
from contextvars import ContextVar

from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, ParamSpec, Sequence, TypeVar
from uuid import uuid4

from bson import BSON, ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DocumentTooLarge

from beanie.odm.enums import SortDirection

from ..common.log import Logger
from ..models import beanie_models as models


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
        self.api: Any | None = None



    def set_api(self, api: Any | None) -> None:
        self.api = api

    @staticmethod
    def _display_reason(meta: models.SnapshotMeta) -> str:
        return ", ".join(meta.reasons).strip()

    @staticmethod
    def _snapshot_history_collection():
        return models.SnapshotHistory.get_pymongo_collection()

    async def _get_snapshot_history(self) -> List[int]:
        doc = await models.SnapshotHistory.find_one(models.SnapshotHistory.key == "default")
        return doc.snapshot_numbers if doc is not None else []

    async def _get_snapshot_settings(self) -> models.SnapshotSettings:
        """Return snapshot settings (with model defaults if AppSettings is missing)."""
        try:
            settings_doc = await models.AppSettings.find_one()
            return settings_doc.snapshots if settings_doc is not None else models.AppSettings().snapshots
        except Exception:
            return models.AppSettings().snapshots

    @staticmethod
    def _compute_modified_snapshots(
        snapshot_history: Sequence[int],
        *,
        current_snapshot_number: int,
        target_snapshot_number: int,
    ) -> List[int]:
        """Compute the navigation path from current -> target.

        This follows the concept in examples/snapshot_algorithm.py:
        - We maintain a persisted `snapshot_history` of snapshot numbers.
        - Restores append `[restore_point, target]` to the history.
        - New snapshots created after a restore create a "jump" from the new
          snapshot number back to the last history number (because snapshot
          numbers keep increasing while the history may end on a smaller number).

        We reconstruct `jump_points` from `snapshot_history` and then traverse:
        candidates from `current` are always `current-1` plus any `jump_points[current]`.
        """

        history = list(snapshot_history)
        if not history:
            raise ValueError("Snapshot history is empty")

        current = current_snapshot_number
        target = target_snapshot_number

        if current == target:
            return [current]

        history_numbers = set(history)
        if current not in history_numbers:
            raise ValueError(f"Current snapshot number {current} not present in snapshot history")
        if target not in history_numbers:
            raise ValueError(f"Target snapshot number {target} not present in snapshot history")

        # jump_points[current] -> list of snapshot numbers you may jump to from `current`
        jump_points: Dict[int, List[int]] = {}
        for prev, nxt in zip(history, history[1:]):
            # Restore points append [restore_point, target] => prev > nxt.
            if nxt < prev:
                jump_points.setdefault(nxt, []).append(prev)
                continue
            # New snapshots created after a restore can create a big step => nxt > prev + 1.
            if nxt > prev + 1:
                jump_points.setdefault(nxt, []).append(prev)

        for key, values in jump_points.items():
            jump_points[key] = sorted(set(values))

        def candidates(current_number: int) -> List[int]:
            options: List[int] = []

            down = current_number - 1
            if down >= target and down in history_numbers:
                options.append(down)

            for jump in jump_points.get(current_number, []):
                if jump >= target and jump in history_numbers:
                    options.append(jump)

            options = sorted(set(options))
            if not options:
                raise ValueError(f"Could not find a suitable snapshot path (current={current_number}, target={target}).")
            return options

        def select_next(options: Sequence[int]) -> int:
            return min(options, key=lambda candidate: candidate - target)

        path: List[int] = [current]
        visited = {current}

        while current != target:
            next_snapshot = select_next(candidates(current))
            if next_snapshot in visited:
                raise ValueError(
                    f"Loop detected while navigating history (current={current}, next={next_snapshot}, target={target})"
                )
            path.append(next_snapshot)
            visited.add(next_snapshot)
            current = next_snapshot

        return path

    async def _append_snapshot_history_numbers(self, snapshot_numbers: Sequence[int]) -> None:
        if not snapshot_numbers:
            return

        await self._snapshot_history_collection().find_one_and_update(
            {"key": "default"},
            {
                "$setOnInsert": {"key": "default"},
                "$push": {"snapshot_numbers": {"$each": list(snapshot_numbers)}},
                "$set": {"updated_at": datetime.now()},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )

    async def _mark_snapshots_obsolete(self, *, snapshot_number: int, history: Sequence[int]) -> List[int]:
        marked: List[int] = []
        for snap in history:
            if snap > snapshot_number and snap not in marked:
                await self.mark_snapshot_as_obsolete(
                    snap,
                    obsoleted_by_snapshot_number=snapshot_number,
                )
                marked.append(snap)
                
        if marked:
            self.logger.debug(
                f"marked_obsolete={marked}",
                extra_tag="SNAPSHOT_RESTORE",
            )

        return sorted(set(marked))

    async def _mark_snapshots_not_obsolete(self, *, snapshot_number: int, history: Sequence[int]) -> List[int]:
        try:
            index = list(history).index(snapshot_number)
        except ValueError:
            return []

        comparing_snapshot = snapshot_number
        marked: List[int] = []

        for i in range(index, 0, -1):
            snap_prev = history[i]
            snap = history[i - 1]

            if snap > snap_prev:
                comparing_snapshot = snap_prev
                continue

            if snap < comparing_snapshot and snap not in marked:
                await self.mark_snapshot_as_not_obsolete(snap)
                marked.append(snap)
                comparing_snapshot = snap

        if marked: 
            self.logger.debug(
                f"marked_not_obsolete={marked}",
                extra_tag="SNAPSHOT_RESTORE",
            )
            

        return sorted(set(marked))

    async def _remove_snapshot_numbers_from_history(self, snapshot_numbers: Sequence[int]) -> None:
        if not snapshot_numbers:
            return

        await self._snapshot_history_collection().update_one(
            {"key": "default"},
            {
                "$setOnInsert": {"key": "default"},
                "$pull": {"snapshot_numbers": {"$in": list(snapshot_numbers)}},
                "$set": {"updated_at": datetime.now()},
            },
            upsert=True,
        )
        
    async def _next_snapshot_number(self) -> int:
        now = datetime.now()
        # Simple approach: snapshot_history owns the counter.
        # If the history doc doesn't exist yet, $inc will create the field.
        doc = await self._snapshot_history_collection().find_one_and_update(
            {"key": "default"},
            {
                "$setOnInsert": {"key": "default", "snapshot_numbers": []},
                "$inc": {"last_snapshot_number": 1},
                "$set": {"updated_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            raise RuntimeError("Failed to allocate snapshot number")
        return int(doc["last_snapshot_number"])

    async def _is_context_snapshot_active(self, context: str | None) -> bool:
        """Return True if a snapshot should be created for this context.

        Supports optional suffixes like `card_closed:<card_id>` by only
        considering the prefix before the first `:`.

        Unknown/None contexts are treated as active (not gated by settings).
        """
        if not context:
            return True

        key = context.split(":", 1)[0]
        settings = await self._get_snapshot_settings()
        dump = settings.model_dump()
        if key not in dump:
            return True
        return bool(dump[key])

    async def list_snapshots(self, *, include_pending: bool = False, limit: int = 50) -> List[models.SnapshotMeta]:
        find_query: Dict[str, Any] = {}
        if not include_pending:
            find_query["status"] = "committed"

        docs = (
            await models.SnapshotMeta.find(find_query)
            .sort(
                [
                    ("snapshot_number", SortDirection.DESCENDING),
                    ("created_at", SortDirection.DESCENDING),
                ]
            )
            .limit(limit)
            .to_list()
        )
        return docs

    async def get_last_loaded_snapshot_meta(self) -> models.SnapshotMeta | None:
        """Return the most recently loaded (restored) snapshot meta, if any.

        We track loads by writing `loaded_at` (and optional `loaded_by_user_id`) onto
        the snapshot's meta document.
        """
        # `loaded_at` is stored as `null` for new snapshots; `$exists` would match those.
        docs = (
            await models.SnapshotMeta.find({"loaded_at": {"$ne": None}})
            .sort(("loaded_at", SortDirection.DESCENDING))
            .limit(1)
            .to_list()
        )
        return docs[0] if docs else None

    async def get_snapshot_meta_by_number(self, snapshot_number: int) -> models.SnapshotMeta | None:
        return await models.SnapshotMeta.find_one(models.SnapshotMeta.snapshot_number == snapshot_number)

    async def get_snapshots_by_number(self, snapshot_numbers: Sequence[int]) -> Dict[int, models.SnapshotMeta]:
        unique = sorted(set(snapshot_numbers))
        if not unique:
            return {}

        docs = await models.SnapshotMeta.find({"snapshot_number": {"$in": unique}}).to_list()
        return {doc.snapshot_number: doc for doc in docs}

    async def mark_snapshot_as_loaded(
        self,
        snapshot_number: int,
        *,
        loaded_by_user_id: int | None = None,
    ) -> None:
        now = datetime.now()
        doc = await models.SnapshotMeta.find_one(models.SnapshotMeta.snapshot_number == snapshot_number)
        if doc is None:
            return

        doc.loaded_at = now
        doc.loaded_by_user_id = loaded_by_user_id
        doc.obsolete = False
        doc.obsoleted_at = None
        doc.obsoleted_by_snapshot_number = None
        await doc.save()

    async def mark_snapshot_as_not_obsolete(self, snapshot_number: int) -> None:
        doc = await models.SnapshotMeta.find_one(models.SnapshotMeta.snapshot_number == snapshot_number,
                                                 models.SnapshotMeta.obsolete == True)
        if doc is None:
            return

        doc.obsolete = False
        doc.obsoleted_at = None
        doc.obsoleted_by_snapshot_number = None
        await doc.save()

    async def mark_snapshot_as_obsolete(
        self,
        snapshot_number: int,
        *,
        obsoleted_by_snapshot_number: int,
    ) -> None:
        now = datetime.now()
        doc = await models.SnapshotMeta.find_one(models.SnapshotMeta.snapshot_number == snapshot_number,
                                                 models.SnapshotMeta.obsolete == False)
        if doc is None:
            return

        doc.obsolete = True
        doc.obsoleted_at = now
        doc.obsoleted_by_snapshot_number = obsoleted_by_snapshot_number
        await doc.save()

    async def _notify_admins_snapshot_loaded(
        self,
        *,
        meta: models.SnapshotMeta,
        loaded_by_user_id: int | None,
    ) -> None:
        api = self.api
        if api is None:
            return

        admin_ids = await api.repo.get_registered_admins()
        if not admin_ids:
            return

        restored_at_text = meta.created_at.strftime("%d.%m.%Y %H:%M")
        reason_text = self._display_reason(meta) or "unknown reason"

        triggered_by_admin = "unknown"
        if loaded_by_user_id is not None:
            try:
                admin_user = await api.repo.find_user_by_id(loaded_by_user_id)
                if admin_user is not None and admin_user.display_name:
                    triggered_by_admin = str(admin_user.display_name)
                else:
                    triggered_by_admin = str(loaded_by_user_id)
            except Exception:
                triggered_by_admin = str(loaded_by_user_id)

        message = (
            "⚠️ **Database restored from snapshot**\n\n"
            f"Snapshot date: {restored_at_text}\n"
            f"State before: {reason_text}\n"
            f"Initiated by admin: {triggered_by_admin}"
        )

        for admin_id in admin_ids:
            try:
                await api.message_manager.send_perm_notification(
                    user_id=int(admin_id),
                    text=message,
                    silent=False,
                )
            except Exception as exc:
                self.logger.warning(
                    f"Failed to send snapshot restore notification to admin {admin_id}: {exc}",
                    extra_tag="SNAPSHOT",
                )

    async def get_snapshot_meta(self, snapshot_id: str) -> models.SnapshotMeta | None:
        return await models.SnapshotMeta.find_one(models.SnapshotMeta.snapshot_id == str(snapshot_id))

    async def capture_snapshot(
        self,
        *,
        snapshot_id: str,
        reason: str,
        context: str | None = None,
        collections: Sequence[str] = DEFAULT_SNAPSHOT_COLLECTIONS,
    ) -> Snapshot:
        created_at = datetime.now()
        docs_by_collection: Dict[str, List[Mapping[str, Any]]] = {}

        for collection_name in collections:
            source = self.db.get_collection(collection_name)
            docs: List[Mapping[str, Any]] = []
            async for doc in source.find({}):
                docs.append(doc)
            docs_by_collection[collection_name] = docs

        total_docs = sum(len(docs) for docs in docs_by_collection.values())

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
        add_to_history: bool = True,
        permanent: bool = False,
    ) -> str:
        current = _append_to_pending_snapshot(reason=reason, context=context)
        if current is not None:
            return current.snapshot_id or ""

        if not await self._is_context_snapshot_active(context):
            return ""

        snapshot_id = uuid4().hex
        snapshot = await self.capture_snapshot(
            snapshot_id=snapshot_id,
            reason=reason,
            context=context,
            collections=collections,
        )
        await self.save_snapshot(
            snapshot,
            save_in_background=save_in_background,
            add_to_history=bool(add_to_history),
            permanent=bool(permanent),
        )
        return snapshot_id

    def pending_snapshot(
        self,
        *,
        reason: str,
        context: str | None = None,
        collections: Sequence[str] = DEFAULT_SNAPSHOT_COLLECTIONS,
        save_in_background: bool = True,
    ) -> "PendingSnapshot":
        return PendingSnapshot(
            self,
            reason=reason,
            context=context,
            collections=collections,
            save_in_background=save_in_background,
        )

    async def save_snapshot(
        self,
        snapshot: Snapshot,
        *,
        save_in_background: bool,
        add_to_history: bool = True,
        permanent: bool = False,
    ) -> None:
        """Saves a captured snapshot and prune old snapshots based on current settings."""

        snapshot_number = await self._next_snapshot_number()

        async def save() -> None:
            keep_last = int((await self._get_snapshot_settings()).keep_last)

            meta_doc: models.SnapshotMeta | None = None
            inserted_chunk_ids: List[ObjectId] = []
            try:
                self.logger.trace(
                    f"Saving snapshot: snapshot_number={snapshot_number}, collections={list(snapshot.collections)}, keep_last={keep_last}",
                    extra_tag="SNAPSHOT",
                )

                meta_doc = models.SnapshotMeta(
                    snapshot_number=snapshot_number,
                    snapshot_id=str(snapshot.snapshot_id),
                    created_at=snapshot.created_at,
                    reasons=list(snapshot.reasons),
                    contexts=list(snapshot.contexts),
                    status="writing",
                    permanent=bool(permanent),
                    obsolete=False,
                    collections={},
                    total_documents=0,
                )
                await meta_doc.insert()

                total_docs = 0
                collections_info: Dict[str, models.SnapshotCollectionChunkInfo] = {}

                for collection_name in snapshot.collections:
                    docs = snapshot.documents_by_collection.get(collection_name, [])
                    info = await self._save_one_collection(
                        snapshot_id=snapshot.snapshot_id,
                        source_collection=collection_name,
                        documents=docs,
                    )
                    inserted_chunk_ids.extend(list(info.chunk_ids))
                    total_docs += info.document_count
                    collections_info[collection_name] = models.SnapshotCollectionChunkInfo(
                        chunk_ids=list(info.chunk_ids),
                        document_count=info.document_count,
                    )

                meta_doc.status = "committed"
                meta_doc.committed_at = datetime.now()
                meta_doc.collections = collections_info
                meta_doc.total_documents = total_docs
                await meta_doc.save()

                if add_to_history:
                    await self._append_snapshot_history_numbers([snapshot_number])

                reason_text = ", ".join(snapshot.reasons).strip() or "(no reason)"
                self.logger.info(
                    f"Snapshot created: #{snapshot_number} {reason_text}, collections={list(snapshot.collections)}",
                    extra_tag="SNAPSHOT",
                )

                self.logger.trace(
                    f"Saved snapshot: snapshot_number={snapshot_number}, total_documents={total_docs}, chunk_count={len(inserted_chunk_ids)}",
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
                        await models.SnapshotDataChunk.find({"_id": {"$in": inserted_chunk_ids}}).delete()
                    if meta_doc is not None:
                        await meta_doc.delete()
                except Exception:
                    pass

        if save_in_background:
            asyncio.create_task(save())
        else:
            await save()

    async def _restore_collection_from_meta(self, meta: models.SnapshotMeta, *, collection_name: str) -> None:
        source_snapshot_id = meta.snapshot_id
        collection_info = meta.collections.get(collection_name)
        if collection_info is None:
            return

        docs = await self._load_snapshot_documents(
            source_snapshot_id,
            collection_name,
            list(collection_info.chunk_ids),
        )

        target_collection = self.db.get_collection(collection_name)
        await target_collection.delete_many({})
        if docs:
            await target_collection.insert_many(docs, ordered=False)

    def _create_restore_plan(
        self,
        *,
        modified_snapshots: Sequence[int],
        snapshots_by_number: Dict[int, models.SnapshotMeta],
        collections_filter: Sequence[str] | None,
    ) -> tuple[set[str], List[int]]:
        modified_collections: List[set[str]] = []
        unique_modified_collections: set[str] = set()

        for snap_num in modified_snapshots:
            snap_meta = snapshots_by_number.get(snap_num)
            if snap_meta is None:
                raise ValueError(f"Snapshot meta missing for snapshot_number={snap_num}")

            snap_collections = {str(name) for name in snap_meta.collections.keys()}
            modified_collections.append(snap_collections)
            unique_modified_collections.update(snap_collections)

        if collections_filter is not None:
            allowed = {str(name) for name in collections_filter if name}
            unique_modified_collections.intersection_update(allowed)

        collected_snapshots: List[int] = []
        collected_collections: set[str] = set()

        for i in range(len(modified_collections) - 1, -1, -1):
            snap_num = modified_snapshots[i]
            snap_collections = modified_collections[i].intersection(unique_modified_collections)

            if snap_collections.difference(collected_collections):
                collected_snapshots.append(snap_num)
                collected_collections.update(snap_collections)

            if collected_collections == unique_modified_collections:
                break

        return unique_modified_collections, collected_snapshots

    async def _create_pre_restore_snapshot(
        self,
        *,
        meta: models.SnapshotMeta,
        snapshot_id: str,
        snapshot_number: int,
        unique_modified_collections: set[str],
    ) -> int | None:
        if not unique_modified_collections:
            return None

        try:
            now = datetime.now()

            referenced_reason = self._display_reason(meta) or str(snapshot_id)
            referenced_reason = f"#{snapshot_number} {referenced_reason}".strip()
            pre_restore_reason = f"Restoration for __{referenced_reason}__"

            pre_restore_snapshot_id = await self.create_snapshot(
                reason=pre_restore_reason,
                context=f"pre_restore:{snapshot_number}",
                collections=sorted(unique_modified_collections),
                save_in_background=False,
                add_to_history=False,
            )

            if not pre_restore_snapshot_id:
                return None

            pre_doc = await self.get_snapshot_meta(str(pre_restore_snapshot_id))
            if pre_doc is None:
                return None

            pre_restore_snapshot_number = pre_doc.snapshot_number
            pre_doc.obsolete = True
            pre_doc.obsoleted_at = now
            pre_doc.obsoleted_by_snapshot_number = snapshot_number
            pre_doc.pre_restore_for_snapshot_number = snapshot_number
            await pre_doc.save()
            return pre_restore_snapshot_number

        except Exception as exc:
            self.logger.error(
                f"Failed to create restoration snapshot; proceeding with restore anyway: {type(exc).__name__}: {exc}",
                extra_tag="SNAPSHOT_RESTORE",
                exc_info=exc,
            )
            return None

    async def _restore_collected_snapshots(
        self,
        *,
        collected_snapshots: Sequence[int],
        snapshots_by_number: Dict[int, models.SnapshotMeta],
        unique_modified_collections: set[str],
    ) -> None:
        # Load collected snapshots from newest to oldest so that older snapshots win.
        for snap_num in collected_snapshots[-1::-1]:
            snap_meta = snapshots_by_number.get(snap_num)
            if snap_meta is None:
                snap_meta = await self.get_snapshot_meta_by_number(snap_num)
            if snap_meta is None:
                raise ValueError(f"Snapshot meta missing for snapshot_number={snap_num}")

            snap_name = self._display_reason(snap_meta) or "unknown"
            snap_collections = sorted(
                [
                    str(name)
                    for name in snap_meta.collections.keys()
                    if str(name) in unique_modified_collections
                ]
            )
            self.logger.debug(
                f"restoring: #{snap_num} {snap_name}, collections: {snap_collections}",
                extra_tag="SNAPSHOT_RESTORE",
            )

            for collection_name in snap_collections:
                await self._restore_collection_from_meta(snap_meta, collection_name=collection_name)

    async def restore_snapshot(
        self,
        snapshot_id: str,
        *,
        collections: Sequence[str] | None = None,
        loaded_by_user_id: int | None = None,
        capture_pre_restore_snapshot: bool = True,
    ) -> None:
        meta = await self.get_snapshot_meta(str(snapshot_id))
        if meta is None:
            raise ValueError(f"Snapshot not found: {snapshot_id}")
        if meta.status != "committed":
            raise ValueError(f"Snapshot is not committed: {snapshot_id}")

        snapshot_number = meta.snapshot_number

        restore_target_name = self._display_reason(meta) or "unknown"
        restore_filter = [str(c) for c in collections] if collections is not None else None
        target_snapshot_collections = [str(name) for name in meta.collections.keys()]
        self.logger.debug(
            f"Restore: #{snapshot_number} {restore_target_name}, snapshot_collections={target_snapshot_collections}, collections_filter={restore_filter}",
            extra_tag="SNAPSHOT_RESTORE",
        )

        if capture_pre_restore_snapshot:
            if any(context.startswith("pre_restore:") for context in meta.contexts):
                capture_pre_restore_snapshot = False

        snapshot_history = await self._get_snapshot_history()
        if not snapshot_history:
            raise ValueError("Snapshot history is empty")

        current_snapshot_number = snapshot_history[-1]
        self.logger.debug(
            f"history_before={snapshot_history}",
            extra_tag="SNAPSHOT_RESTORE",
        )
        if snapshot_number not in snapshot_history:
            raise ValueError(f"Snapshot number {snapshot_number} does not exist in the snapshot history.")

        # Collect all snapshots that were modified since the current state,
        # navigating the history with shortcuts/restoration points.
        modified_snapshots = self._compute_modified_snapshots(
            snapshot_history,
            current_snapshot_number=current_snapshot_number,
            target_snapshot_number=snapshot_number,
        )

        self.logger.debug(
            f"modified_snapshots={modified_snapshots}",
            extra_tag="SNAPSHOT_RESTORE",
        )

        snapshots_by_number = await self.get_snapshots_by_number(modified_snapshots)

        unique_modified_collections, collected_snapshots = self._create_restore_plan(
            modified_snapshots=modified_snapshots,
            snapshots_by_number=snapshots_by_number,
            collections_filter=collections,
        )

        self.logger.debug(
            f"collected_collections={sorted(unique_modified_collections)}",
            extra_tag="SNAPSHOT_RESTORE",
        )

        self.logger.debug(
            f"collected_snapshots={collected_snapshots}",
            extra_tag="SNAPSHOT_RESTORE",
        )
        pre_restore_snapshot_number = None
        if capture_pre_restore_snapshot:
            pre_restore_snapshot_number = await self._create_pre_restore_snapshot(
                meta=meta,
                snapshot_id=str(snapshot_id),
                snapshot_number=snapshot_number,
                unique_modified_collections=unique_modified_collections,
            )

        await self._restore_collected_snapshots(
            collected_snapshots=collected_snapshots,
            snapshots_by_number=snapshots_by_number,
            unique_modified_collections=unique_modified_collections,
        )

        await self.mark_snapshot_as_loaded(snapshot_number, loaded_by_user_id=loaded_by_user_id)

        # Previous version:
        #
        # if len(modified_snapshots) >= 2:
        #     last_snapshot_number = modified_snapshots[-1]
        #
        #     marked_not_obsolete: List[int] = []
        #     marked_obsolete: List[int] = []
        #
        #     for snap_num in modified_snapshots[-2::-1]:
        #         if snap_num <= last_snapshot_number:
        #             await self.mark_snapshot_as_not_obsolete(snap_num)
        #             marked_not_obsolete.append(snap_num)
        #             last_snapshot_number = snap_num
        #         else:
        #             break
        #
        #     for snap_num in modified_snapshots:
        #         if snap_num > snapshot_number:
        #             await self.mark_snapshot_as_obsolete(
        #                 snap_num,
        #                 obsoleted_by_snapshot_number=snapshot_number,
        #             )
        #             marked_obsolete.append(snap_num)
        #
        #     if marked_not_obsolete:
        #         self.logger.trace(
        #             f"marked_not_obsolete={sorted(set(marked_not_obsolete))}",
        #             extra_tag="SNAPSHOT_RESTORE",
        #         )
        #     if marked_obsolete:
        #         self.logger.trace(
        #             f"marked_obsolete={sorted(set(marked_obsolete))}",
        #             extra_tag="SNAPSHOT_RESTORE",
        #         )

        # New version: unmark based on first occurrence in full history, mark obsolete based on modified_snapshots.
        marked_not_obsolete = await self._mark_snapshots_not_obsolete(
            snapshot_number=snapshot_number,
            history=snapshot_history,
        )

        marked_obsolete = await self._mark_snapshots_obsolete(
            snapshot_number=snapshot_number,
            history=modified_snapshots,
        )


        # Append only the restoration point and the final loaded snapshot.
        history_append: List[int] = []
        if pre_restore_snapshot_number is not None:
            history_append.append(pre_restore_snapshot_number)
        history_append.append(snapshot_number)
        await self._append_snapshot_history_numbers(history_append)

        history_after = await self._get_snapshot_history()
        self.logger.debug(
            f"history_after={history_after}",
            extra_tag="SNAPSHOT_RESTORE",
        )

        await self._notify_admins_snapshot_loaded(
            meta=meta,
            loaded_by_user_id=loaded_by_user_id,
        )



    async def delete_snapshot(self, snapshot_id: str) -> bool:
        doc = await models.SnapshotMeta.find_one(models.SnapshotMeta.snapshot_id == str(snapshot_id))
        if doc is None:
            return False

        await self._remove_snapshot_numbers_from_history([doc.snapshot_number])

        await self._delete_snapshot_doc(doc)
        return True

    async def clear_obsolete_snapshots(self) -> Dict[str, int]:
        """Delete obsolete (non-permanent) snapshots and remove them from history."""
        self.logger.warning("Clearing obsolete snapshots (meta + data)", extra_tag="SNAPSHOT")

        to_delete = (
            await models.SnapshotMeta.find(
                {
                    "status": "committed",
                    "obsolete": True,
                    "permanent": False,
                }
            )
            .sort(("snapshot_number", SortDirection.ASCENDING))
            .to_list()
        )

        deleted_meta = 0
        deleted_data = 0
        numbers_to_remove: List[int] = []

        for meta in to_delete:
            numbers_to_remove.append(meta.snapshot_number)

            collections = meta.collections or {}
            for info in collections.values():
                deleted_data += len(info.chunk_ids or [])

            await self._delete_snapshot_doc(meta)
            deleted_meta += 1

        if numbers_to_remove:
            await self._remove_snapshot_numbers_from_history(numbers_to_remove)

        result = {
            "deleted_meta": deleted_meta,
            "deleted_data": deleted_data,
        }
        self.logger.warning(f"Cleared obsolete snapshots: {result}", extra_tag="SNAPSHOT")
        return result

    async def clear_all_snapshots(self) -> Dict[str, int]:
        """Delete all snapshot metadata and snapshot data documents.

        This removes ALL documents from `snapshots_meta` and `snapshots_data`.
        Returns counts for UI reporting.
        """
        self.logger.warning("Clearing ALL snapshots (meta + data)", extra_tag="SNAPSHOT")

        meta_count = await models.SnapshotMeta.count()
        data_count = await models.SnapshotDataChunk.count()

        await models.SnapshotDataChunk.delete_all()
        await models.SnapshotMeta.delete_all()

        # Clear snapshot history (includes the snapshot counter).
        await models.SnapshotHistory.delete_all()

        result = {
            "deleted_meta": meta_count,
            "deleted_data": data_count,
        }
        self.logger.warning(f"Cleared snapshots: {result}", extra_tag="SNAPSHOT")
        return result

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
        payload: Dict[str, Any] = {
            "snapshot_id": snapshot_id,
            "source_collection": source_collection,
            "chunk_index": chunk_index,
            "created_at": datetime.now(),
            "document_count": len(documents),
            "documents": [dict(d) for d in documents],
        }

        if len(BSON.encode(payload)) > self.max_bson_bytes:
            raise DocumentTooLarge("Snapshot chunk exceeds BSON size limit")

        chunk = models.SnapshotDataChunk(**payload)
        await chunk.insert()
        if chunk.id is None:
            raise RuntimeError("SnapshotDataChunk insert did not return an _id")
        return ObjectId(str(chunk.id))

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

        self.logger.trace(
            f"Chunk split required: collection={source_collection}, chunk_index={chunk_index}, documents={len(documents)}",
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

        chunks = (
            await models.SnapshotDataChunk.find(
                {
                    "snapshot_id": snapshot_id,
                    "source_collection": source_collection,
                    "_id": {"$in": list(chunk_ids)},
                }
            )
            .sort(("chunk_index", SortDirection.ASCENDING))
            .to_list()
        )

        docs: List[Mapping[str, Any]] = []
        for chunk in chunks:
            docs.extend(list(chunk.documents or []))
        return docs

    async def _delete_snapshot_doc(self, meta: models.SnapshotMeta) -> None:
        ids: List[ObjectId] = []
        collections = meta.collections or {}
        for info in collections.values():
            ids.extend(list(info.chunk_ids or []))

        if ids:
            await models.SnapshotDataChunk.find({"_id": {"$in": ids}}).delete()
        await meta.delete()

    async def _prune_old_snapshots(self, *, keep_last: int) -> None:
        if keep_last <= 0:
            return

        to_delete = (
            await models.SnapshotMeta.find(
                {
                    "status": "committed",
                    "permanent": False,
                }
            )
            .sort(("snapshot_number", SortDirection.DESCENDING))
            .skip(keep_last)
            .to_list()
        )

        numbers_to_remove: List[int] = []
        for meta in to_delete:
            numbers_to_remove.append(meta.snapshot_number)
            await self._delete_snapshot_doc(meta)

        if numbers_to_remove:
            await self._remove_snapshot_numbers_from_history(numbers_to_remove)


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

        if not await self.manager._is_context_snapshot_active(self.context):
            self._snapshot = None
            self.snapshot_id = None
            return self

        self.snapshot_id = uuid4().hex

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


    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if self._nested_under is not None:
            return False

        if exc_type is not None or self._aborted:
            self._snapshot = None
            if self._token is not None:
                _CURRENT_PENDING_SNAPSHOT.reset(self._token)
                self._token = None
            return False

        if not self._snapshot:
            return False

        await self.manager.save_snapshot(self._snapshot, save_in_background=self.save_in_background)
        self._snapshot = None
        if self._token is not None:
            _CURRENT_PENDING_SNAPSHOT.reset(self._token)
            self._token = None
        return False


def pending_snapshot(
    context: str | Callable[..., str],
    *,
    reason: str | Callable[..., str] | None = None,
    collections: Sequence[str] | Callable[..., Sequence[str]] | None = None,
    save_in_background: bool = True,
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
            computed_collections = collections(*args, **kwargs) if callable(collections) else collections

            async with snapshot_manager.pending_snapshot(
                reason=computed_reason,
                context=computed_context,
                collections=computed_collections or DEFAULT_SNAPSHOT_COLLECTIONS,
                save_in_background=bool(save_in_background),
            ):
                return await func(*args, **kwargs)

        return wrapper

    return decorator
