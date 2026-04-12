from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from beanie.odm.enums import SortDirection

from src.common.log import Logger
from src.database.snapshot_manager import SnapshotManager
from src.models import beanie_models as models


logger = Logger("WeeklySnapshots")

WEEKLY_FULL_SNAPSHOT_CONTEXT = "weekly_full_snapshot"
WEEKLY_FULL_SNAPSHOT_REASON = "Weekly full snapshot"
WEEKLY_INTERVAL = timedelta(days=7)


async def _get_latest_weekly_snapshot() -> models.SnapshotMeta | None:
    docs = (
        await models.SnapshotMeta.find(
            {
                "status": "committed",
                "permanent": True,
                "contexts": {"$in": [WEEKLY_FULL_SNAPSHOT_CONTEXT]},
            }
        )
        .sort(("created_at", SortDirection.DESCENDING))
        .limit(1)
        .to_list()
    )
    return docs[0] if docs else None


async def run_periodic_weekly_full_snapshots(
    *,
    stop_event: asyncio.Event,
    snapshot_manager: SnapshotManager,
) -> None:
    """Create a permanent full snapshot roughly once per week.

    The snapshot is stored like any other snapshot (appears in history and can be restored),
    but it is marked permanent so retention pruning never deletes it.
    """

    logger.info("Starting periodic weekly full snapshots")

    while not stop_event.is_set():
        try:
            last_weekly = await _get_latest_weekly_snapshot()
            now = datetime.now()

            due_at = (last_weekly.created_at + WEEKLY_INTERVAL) if last_weekly is not None else None
            if due_at is None or now >= due_at:
                logger.info("Creating weekly full snapshot")
                await snapshot_manager.create_snapshot(
                    reason=WEEKLY_FULL_SNAPSHOT_REASON,
                    context=WEEKLY_FULL_SNAPSHOT_CONTEXT,
                    save_in_background=False,
                    permanent=True,
                    full_snapshot=True,
                )
                continue

            # Poll at most hourly so stop_event cancels quickly.
            sleep_seconds = max(60, min(3600, int((due_at - now).total_seconds())))
            await asyncio.wait_for(stop_event.wait(), timeout=sleep_seconds)

        except TimeoutError:
            continue
        except Exception as exc:
            logger.error(
                f"Weekly snapshot task error: {type(exc).__name__}: {exc!r}",
                exc_info=exc,
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=60)
            except TimeoutError:
                continue
