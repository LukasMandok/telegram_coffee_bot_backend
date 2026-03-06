"""Snapshots conversation flow (MessageFlow-based).

Implements the `/snapshots` admin command:
- Create a manual snapshot
- List snapshots and restore any of them via buttons

This is intentionally minimal: a single menu + a paginated restore list.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from ..message_flow import (
    ButtonCallback,
    MessageDefinition,
    MessageFlow,
    PaginationConfig,
    StateType,
)
from ..message_flow_helpers import NavigationButtons
from ...services.gsheet_sync import request_gsheet_sync_after_action


SNAPSHOT_REASON_MANUAL = "Manual snapshot"


def _format_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d.%m. %H:%M")
    return str(value)


def _display_reason(meta: Mapping[str, Any]) -> str:
    reasons = meta.get("reasons")
    if isinstance(reasons, list) and reasons:
        return ", ".join(str(r) for r in reasons if r is not None)

    reason = meta.get("reason")
    return str(reason) if reason else ""


def _short_id(snapshot_id: str) -> str:
    return snapshot_id[:8] if snapshot_id else ""


async def create_manual_snapshot(flow_state, api, user_id) -> Optional[str]:
    snapshot_manager = api.get_snapshot_manager()

    snapshot_id = await snapshot_manager.create_snapshot(
        reason=SNAPSHOT_REASON_MANUAL,
        context="manual_snapshot",
        save_in_background=False,
    )

    meta = await snapshot_manager.get_snapshot_meta(snapshot_id)
    flow_state.set("created_snapshot_id", snapshot_id)
    flow_state.set("created_snapshot_meta", meta)

    return "create_result"


async def restore_selected(flow_state, api, user_id) -> None:
    snapshot_id = flow_state.get("restore_snapshot_id")
    if not snapshot_id:
        flow_state.set("restore_error", "No snapshot selected")
        return

    snapshot_manager = api.get_snapshot_manager()
    try:
        await snapshot_manager.restore_snapshot(str(snapshot_id))

        try:
            await snapshot_manager.mark_snapshot_loaded(str(snapshot_id), loaded_by_user_id=int(user_id))
        except Exception:
            pass

        # Restore rewrites Mongo collections, but some managers keep in-memory caches.
        # Refresh them so bot behavior matches the restored DB without restart.
        try:
            await api.coffee_card_manager.load_from_db()
        except Exception:
            pass

        request_gsheet_sync_after_action(reason="snapshot_restored")
        flow_state.set("restore_error", None)
    except Exception as exc:  # pragma: no cover
        flow_state.set("restore_error", f"{type(exc).__name__}: {exc}")


async def build_main_text(flow_state, api, user_id) -> str:
    return (
        "🧷 **Snapshots**\n\n"
        "Create and restore database snapshots.\n\n"
        "What would you like to do?"
    )


async def build_main_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
    return [
        [ButtonCallback("📸 Create manual snapshot", "create", callback_handler=create_manual_snapshot)],
        [ButtonCallback("↩️ Restore snapshot", "restore")],
        [ButtonCallback("🗑️ Clear all snapshots", "clear_all")],
        NavigationButtons.close(),
    ]


async def clear_all_snapshots(flow_state, api, user_id) -> None:
    snapshot_manager = api.get_snapshot_manager()
    try:
        result = await snapshot_manager.clear_all_snapshots()
        flow_state.set("clear_all_result", result)
        flow_state.set("clear_all_error", None)
        # Ensure pagination cache doesn't show stale items when user goes back.
        flow_state.pagination_state.pop("restore_list", None)
    except Exception as exc:  # pragma: no cover
        flow_state.set("clear_all_result", None)
        flow_state.set("clear_all_error", f"{type(exc).__name__}: {exc}")


async def build_create_result_text(flow_state, api, user_id) -> str:
    snapshot_id = flow_state.get("created_snapshot_id", "")
    meta = flow_state.get("created_snapshot_meta")

    created_at = _format_date(meta.get("created_at")) if isinstance(meta, Mapping) else "(unknown)"
    reason = (
        _display_reason(meta) if isinstance(meta, Mapping) else SNAPSHOT_REASON_MANUAL
    )
    short_id = _short_id(str(snapshot_id))

    return (
        "✅ **Snapshot created**\n\n"
        f"ID: `{snapshot_id}`\n"
        f"Short: `{short_id}`\n"
        f"Created: {created_at}\n"
        f"Reason: {reason}"
    )


async def list_snapshots(flow_state, api, user_id) -> List[Dict[str, Any]]:
    snapshot_manager = api.get_snapshot_manager()
    snapshots = await snapshot_manager.list_snapshots(include_pending=False, limit=50)

    latest_loaded_id: str | None = None
    try:
        loaded_meta = await snapshot_manager.get_last_loaded_snapshot_meta()
        if isinstance(loaded_meta, Mapping) and loaded_meta.get("snapshot_id"):
            latest_loaded_id = str(loaded_meta.get("snapshot_id"))
    except Exception:
        latest_loaded_id = None

    for item in snapshots:
        try:
            item_id = str(item.get("snapshot_id"))
            if latest_loaded_id and item_id == latest_loaded_id:
                item["_snapshot_loaded_latest"] = True
                continue
            if item.get("loaded_at") is not None:
                item["_snapshot_loaded_before"] = True
        except Exception:
            pass

    # Clear existing pagination cache so list stays fresh if user returns.
    flow_state.pagination_state.pop("restore_list", None)
    return snapshots


def format_snapshot_line(item: Dict[str, Any], idx: int) -> str:
    reason = _display_reason(item)
    created_at = _format_date(item.get("created_at"))

    is_obsolete = bool(item.get("obsolete"))
    is_latest_loaded = bool(item.get("_snapshot_loaded_latest"))
    is_previously_loaded = bool(item.get("_snapshot_loaded_before"))

    if is_obsolete:
        return f"{idx + 1}. 🚫 **{reason}** - {created_at}"
    if is_latest_loaded:
        return f"{idx + 1}. ↩️ **{reason}** - {created_at}"
    if is_previously_loaded:
        return f"{idx + 1}. ⬅ **{reason}** - {created_at}"

    return f"{idx + 1}. **{reason}** - {created_at}"


def build_restore_button(item: Dict[str, Any], idx: int) -> ButtonCallback:
    snapshot_id = str(item.get("snapshot_id", ""))
    created_at = item.get("created_at")

    # Button label requirements:
    # - no icon
    # - no 'restore' text
    # - show number and date/time (no seconds)
    label = f"{idx + 1}. {_format_date(created_at)}"

    async def select_and_confirm(flow_state, api, user_id) -> Optional[str]:
        flow_state.set("restore_snapshot_id", snapshot_id)
        flow_state.set("restore_snapshot_index", idx + 1)
        flow_state.set("restore_error", None)
        return "restore_confirm"

    return ButtonCallback(label, f"restore:{snapshot_id}", callback_handler=select_and_confirm)


async def build_restore_confirmation(flow_state, api, user_id) -> str:
    snapshot_id = flow_state.get("restore_snapshot_id", "")
    snapshot_index = flow_state.get("restore_snapshot_index")
    if not snapshot_id:
        return "No snapshot selected."

    snapshot_manager = api.get_snapshot_manager()
    meta = await snapshot_manager.get_snapshot_meta(str(snapshot_id))
    created_at = _format_date(meta.get("created_at")) if isinstance(meta, Mapping) else "(unknown)"
    reason = (
        _display_reason(meta) if isinstance(meta, Mapping) else "(unknown)"
    )

    cards_count: str = "?"
    if isinstance(meta, Mapping):
        collections = meta.get("collections")
        if isinstance(collections, Mapping):
            coffee_cards = collections.get("coffee_cards")
            if isinstance(coffee_cards, Mapping):
                doc_count = coffee_cards.get("document_count")
                if doc_count is not None:
                    cards_count = str(doc_count)

    prefix = f"Snapshot #{snapshot_index}\n\n" if snapshot_index else ""

    return (
        f"{prefix}"
        "Restore the database to this snapshot?\n\n"
        f"Created: {created_at}\n"
        f"Reason: {reason}\n"
        f"Cards in snapshot: {cards_count}"
    )


async def build_restore_list_text(flow_state, api, user_id) -> str:
    return (
        "**Select a snapshot**\n\n"
        "Choose a snapshot number to restore.\n\n"
        "↩️: loaded   🚫: obsolete   \n⬅: previously loaded"
    )


async def build_restore_result_text(flow_state, api, user_id) -> str:
    snapshot_id = flow_state.get("restore_snapshot_id", "")
    err = flow_state.get("restore_error")

    if err:
        return (
            "❌ **Restore failed**\n\n"
            f"Snapshot: `{snapshot_id}`\n"
            f"Error: {err}"
        )

    return (
        "✅ **Restore completed**\n\n"
        f"Restored to snapshot: `{snapshot_id}`"
    )


async def build_clear_all_result_text(flow_state, api, user_id) -> str:
    err = flow_state.get("clear_all_error")
    result = flow_state.get("clear_all_result")

    if err:
        return "❌ **Clear failed**\n\n" f"Error: {err}"

    deleted_meta = "?"
    deleted_data = "?"
    if isinstance(result, Mapping):
        deleted_meta = str(result.get("deleted_meta", "?"))
        deleted_data = str(result.get("deleted_data", "?"))

    return (
        "✅ **All snapshots cleared**\n\n"
        f"Deleted meta docs: {deleted_meta}\n"
        f"Deleted data docs: {deleted_data}"
    )


def create_snapshots_flow() -> MessageFlow:
    flow = MessageFlow()

    flow.add_state(
        MessageDefinition(
            state_id="main",
            state_type=StateType.BUTTON,
            text_builder=build_main_text,
            keyboard_builder=build_main_keyboard,
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id="create_result",
            state_type=StateType.BUTTON,
            text_builder=build_create_result_text,
            buttons=None,
            auto_exit_after_render=True,
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id="restore_list",
            state_type=StateType.BUTTON,
            text_builder=build_restore_list_text,
            # 2-column grid, 5 rows per page => 10 items.
            pagination_config=PaginationConfig(page_size=10, items_per_row=2, close_button_text="◁ Back"),
            pagination_items_builder=list_snapshots,
            pagination_item_formatter=format_snapshot_line,
            pagination_item_button_builder=build_restore_button,
            exit_buttons=[],
            next_state_map={"close": "main"},
        )
    )

    flow.add_confirmation(
        state_id="clear_all_confirm_1",
        question=(
            "⚠️ **Delete ALL snapshots?**\n\n"
            "This will permanently delete all snapshot history."
        ),
        on_confirm_state="clear_all_confirm_2",
        on_cancel_state="main",
        confirm_text="Continue",
        cancel_text="◁ Back",
        warning="This cannot be undone.",
    )

    flow.add_confirmation(
        state_id="clear_all_confirm_2",
        question=(
            "🚨 **Final confirmation**\n\n"
            "Really delete ALL snapshots?"
        ),
        on_confirm_state="clear_all_result",
        on_cancel_state="main",
        confirm_text="✅ Yes, delete all",
        cancel_text="◁ Back",
        warning="This cannot be undone.",
    )

    flow.add_state(
        MessageDefinition(
            state_id="clear_all_result",
            state_type=StateType.BUTTON,
            text_builder=build_clear_all_result_text,
            buttons=None,
            auto_exit_after_render=True,
            on_enter=clear_all_snapshots,
        )
    )

    flow.add_confirmation(
        state_id="restore_confirm",
        question=build_restore_confirmation,
        on_confirm_state="restore_result",
        on_cancel_state="restore_list",
        confirm_text="✅ Restore",
        cancel_text="◁ Back",
        warning="⚠️ This will overwrite the current database state.",
    )

    flow.add_state(
        MessageDefinition(
            state_id="restore_result",
            state_type=StateType.BUTTON,
            text_builder=build_restore_result_text,
            buttons=None,
            auto_exit_after_render=True,
            on_enter=restore_selected,
        )
    )

    # Simple transitions
    flow.states["main"].next_state_map.update({"restore": "restore_list", "clear_all": "clear_all_confirm_1"})

    return flow
