"""Snapshots conversation flow (MessageFlow-based).

Implements the `/snapshots` admin command:
- Create a manual snapshot
- List snapshots and restore any of them via buttons

This is intentionally minimal: a single menu + a paginated restore list.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional

from ..message_flow import (
    ButtonCallback,
    MessageDefinition,
    MessageFlow,
    PaginationConfig,
    RegexValidator,
    StateType,
)
from ..message_flow_helpers import CommonCallbacks, NavigationButtons, build_text_input_handler
from ...services.gsheet_sync import request_gsheet_sync_after_action
from ...models import beanie_models as models


STATE_MAIN = "main"
STATE_CLEANUP_MENU = "cleanup_menu"
STATE_CREATE_RESULT = "create_result"
STATE_RESTORE_LIST = "restore_list"
STATE_RESTORE_CONFIRM = "restore_confirm"
STATE_RESTORE_RESULT = "restore_result"

STATE_CLEAR_ALL_CONFIRM_1 = "clear_all_confirm_1"
STATE_CLEAR_ALL_CONFIRM_2 = "clear_all_confirm_2"
STATE_CLEAR_ALL_RESULT = "clear_all_result"

STATE_CLEAR_OBSOLETE_CONFIRM = "clear_obsolete_confirm"
STATE_CLEAR_OBSOLETE_RESULT = "clear_obsolete_result"

CB_CREATE = "create"
CB_RESTORE = "restore"
CB_CLEANUP = "cleanup"

CB_CLEAR_ALL = "clear_all"
CB_CLEAR_OBSOLETE = "clear_obsolete"

CB_RESTORE_PREFIX = "restore:"

KEY_CREATED_SNAPSHOT_ID = "created_snapshot_id"
KEY_CREATED_SNAPSHOT_META = "created_snapshot_meta"

KEY_RESTORE_SNAPSHOT_ID = "restore_snapshot_id"
KEY_RESTORE_SNAPSHOT_NUMBER = "restore_snapshot_number"
KEY_RESTORE_ERROR = "restore_error"

KEY_CLEAR_ALL_RESULT = "clear_all_result"
KEY_CLEAR_ALL_ERROR = "clear_all_error"
KEY_CLEAR_OBSOLETE_RESULT = "clear_obsolete_result"
KEY_CLEAR_OBSOLETE_ERROR = "clear_obsolete_error"


SNAPSHOT_REASON_MANUAL = "Manual snapshot"


@dataclass(frozen=True)
class SnapshotListItem:
    meta: models.SnapshotMeta
    is_latest_loaded: bool
    is_previously_loaded: bool


def _format_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d.%m. %H:%M")
    return str(value)


def _display_reason(meta: models.SnapshotMeta) -> str:
    return ", ".join(meta.reasons).strip()


def _short_id(snapshot_id: str) -> str:
    return snapshot_id[:8] if snapshot_id else ""


async def create_manual_snapshot(flow_state, api, user_id) -> Optional[str]:
    snapshot_manager = api.get_snapshot_manager()

    snapshot_id = await snapshot_manager.create_snapshot(
        reason=SNAPSHOT_REASON_MANUAL,
        context="manual_snapshot",
        save_in_background=False,
        permanent=True,
        full_snapshot=True,
    )

    meta = await snapshot_manager.get_snapshot_meta(snapshot_id) if snapshot_id else None
    flow_state.set(KEY_CREATED_SNAPSHOT_ID, snapshot_id)
    flow_state.set(KEY_CREATED_SNAPSHOT_META, meta)

    return STATE_CREATE_RESULT


async def restore_selected(flow_state, api, user_id) -> None:
    snapshot_id = flow_state.get(KEY_RESTORE_SNAPSHOT_ID)
    if not snapshot_id:
        flow_state.set(KEY_RESTORE_ERROR, "No snapshot selected")
        return

    snapshot_manager = api.get_snapshot_manager()

    try:
        await snapshot_manager.restore_snapshot(
            str(snapshot_id),
            loaded_by_user_id=int(user_id),
        )

        # Restore rewrites Mongo collections, but some managers keep in-memory caches.
        # Refresh them so bot behavior matches the restored DB without restart.
        try:
            await api.coffee_card_manager.load_from_db()
        except Exception:
            pass

        request_gsheet_sync_after_action(reason="snapshot_restored")
        flow_state.set(KEY_RESTORE_ERROR, None)
    except Exception as exc:  # pragma: no cover
        flow_state.set(KEY_RESTORE_ERROR, f"{type(exc).__name__}: {exc}")


async def build_main_text(flow_state, api, user_id) -> str:
    return (
        "🧷 **Snapshots**\n\n"
        "Create and restore database snapshots.\n\n"
        "What would you like to do?"
    )


async def build_main_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
    return [
        [ButtonCallback("📸 Create manual snapshot", CB_CREATE, callback_handler=create_manual_snapshot)],
        [ButtonCallback("↩️ Restore snapshot", CB_RESTORE)],
        [ButtonCallback("🧹 Cleanup", CB_CLEANUP)],
        NavigationButtons.close(),
    ]


async def build_cleanup_text(flow_state, api, user_id) -> str:
    return (
        "🧹 **Cleanup**\n\n"
        "Delete snapshots to free up space.\n\n"
        "What would you like to delete?"
    )


async def build_cleanup_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
    return [
        [ButtonCallback("🗑️ Delete ALL snapshots", CB_CLEAR_ALL)],
        [ButtonCallback("🚫 Delete obsolete snapshots", CB_CLEAR_OBSOLETE)],
        [ButtonCallback("◁ Back", CommonCallbacks.BACK)],
    ]


async def clear_all_snapshots(flow_state, api, user_id) -> None:
    snapshot_manager = api.get_snapshot_manager()
    try:
        result = await snapshot_manager.clear_all_snapshots()
        flow_state.set(KEY_CLEAR_ALL_RESULT, result)
        flow_state.set(KEY_CLEAR_ALL_ERROR, None)
        # Ensure pagination cache doesn't show stale items when user goes back.
        flow_state.pagination_state.pop(STATE_RESTORE_LIST, None)
    except Exception as exc:  # pragma: no cover
        flow_state.set(KEY_CLEAR_ALL_RESULT, None)
        flow_state.set(KEY_CLEAR_ALL_ERROR, f"{type(exc).__name__}: {exc}")


async def clear_obsolete_snapshots(flow_state, api, user_id) -> None:
    snapshot_manager = api.get_snapshot_manager()
    try:
        result = await snapshot_manager.clear_obsolete_snapshots()
        flow_state.set(KEY_CLEAR_OBSOLETE_RESULT, result)
        flow_state.set(KEY_CLEAR_OBSOLETE_ERROR, None)
        # Ensure pagination cache doesn't show stale items when user goes back.
        flow_state.pagination_state.pop(STATE_RESTORE_LIST, None)
    except Exception as exc:  # pragma: no cover
        flow_state.set(KEY_CLEAR_OBSOLETE_RESULT, None)
        flow_state.set(KEY_CLEAR_OBSOLETE_ERROR, f"{type(exc).__name__}: {exc}")


async def build_create_result_text(flow_state, api, user_id) -> str:
    snapshot_id = flow_state.get(KEY_CREATED_SNAPSHOT_ID, "")
    meta: models.SnapshotMeta | None = flow_state.get(KEY_CREATED_SNAPSHOT_META)

    created_at = _format_date(meta.created_at) if meta is not None else "(unknown)"
    reason = _display_reason(meta) if meta is not None else SNAPSHOT_REASON_MANUAL
    short_id = _short_id(str(snapshot_id))

    return (
        "✅ **Snapshot created**\n\n"
        f"ID: `{snapshot_id}`\n"
        f"Short: `{short_id}`\n"
        f"Created: {created_at}\n"
        f"Reason: {reason}"
    )


async def list_snapshots(flow_state, api, user_id) -> List[SnapshotListItem]:
    snapshot_manager = api.get_snapshot_manager()
    snapshots = await snapshot_manager.list_snapshots(include_pending=False, limit=50)

    loaded_meta = await snapshot_manager.get_last_loaded_snapshot_meta()
    latest_loaded_id = loaded_meta.snapshot_id if loaded_meta is not None else None

    items: List[SnapshotListItem] = []
    for meta in snapshots:
        is_latest_loaded = latest_loaded_id is not None and meta.snapshot_id == latest_loaded_id
        is_previously_loaded = meta.loaded_at is not None and not is_latest_loaded
        items.append(
            SnapshotListItem(
                meta=meta,
                is_latest_loaded=is_latest_loaded,
                is_previously_loaded=is_previously_loaded,
            )
        )

    # Clear existing pagination cache so list stays fresh if user returns.
    flow_state.pagination_state.pop(STATE_RESTORE_LIST, None)
    return items


def format_snapshot_line(item: SnapshotListItem, idx: int) -> str:
    meta = item.meta
    reason = _display_reason(meta)
    created_at = _format_date(meta.created_at)

    number_text = str(meta.snapshot_number)

    is_obsolete = meta.obsolete
    is_latest_loaded = item.is_latest_loaded
    is_previously_loaded = item.is_previously_loaded
    
    text = f"**{number_text}.**"
    
    text += f"\u2003{created_at}\u2003"

    if is_latest_loaded:
        text += f" ↩️"
    elif is_previously_loaded:
        text += f" ⬅"
    
    if is_obsolete:
        text += f" 🚫"
        text += f"\n\u2003 **~~{reason}~~**"
    else:
        text += f"\n\u2003 **{reason}**"

    return text


def build_restore_button(item: SnapshotListItem, idx: int) -> ButtonCallback:
    meta = item.meta
    snapshot_id = meta.snapshot_id

    snapshot_number = meta.snapshot_number
    number_text = str(snapshot_number)

    # Button label requirements:
    # - no icon
    # - no 'restore' text
    # - show only the snapshot number
    label = number_text

    async def select_and_confirm(flow_state, api, user_id) -> Optional[str]:
        flow_state.set(KEY_RESTORE_SNAPSHOT_ID, snapshot_id)
        flow_state.set(KEY_RESTORE_SNAPSHOT_NUMBER, snapshot_number)
        flow_state.set(KEY_RESTORE_ERROR, None)
        return STATE_RESTORE_CONFIRM

    return ButtonCallback(label, f"{CB_RESTORE_PREFIX}{snapshot_id}", callback_handler=select_and_confirm)


async def build_restore_confirmation(flow_state, api, user_id) -> str:
    snapshot_id = flow_state.get(KEY_RESTORE_SNAPSHOT_ID, "")
    snapshot_number = flow_state.get(KEY_RESTORE_SNAPSHOT_NUMBER)
    if not snapshot_id:
        return "No snapshot selected."

    snapshot_manager = api.get_snapshot_manager()
    meta = await snapshot_manager.get_snapshot_meta(str(snapshot_id))
    created_at = _format_date(meta.created_at) if meta is not None else "(unknown)"
    reason = _display_reason(meta) if meta is not None else "(unknown)"

    cards_info = "(unknown)"
    if meta is not None:
        coffee_cards = meta.collections.get("coffee_cards")
        if coffee_cards is not None:
            cards_info = str(coffee_cards.document_count)
        elif meta.collections:
            cards_info = ", ".join(sorted(meta.collections.keys()))
        else:
            cards_info = "(empty)"

    prefix = f"Snapshot #{snapshot_number}\n\n" if snapshot_number else ""

    return (
        f"{prefix}"
        "Restore the database to this snapshot?\n\n"
        f"Created: {created_at}\n"
        f"Reason: {reason}\n"
        f"Collections: {cards_info}"
    )


async def build_restore_list_text(flow_state, api, user_id) -> str:
    return (
        "**Select a snapshot**\n\n"
        "Choose a snapshot number to restore (button or type the number).\n\n"
        "↩️: loaded   ~~obsolete~~   \n⬅: previously loaded"
    )


async def resolve_snapshot_number_matches(
    typed_number: int,
    flow_state,
    api,
    user_id: int,
) -> List[models.SnapshotMeta]:
    snapshot_manager = api.get_snapshot_manager()
    meta = await snapshot_manager.get_snapshot_meta_by_number(int(typed_number))
    return [meta] if meta is not None else []


async def _select_snapshot_from_match(meta: models.SnapshotMeta, flow_state, api, user_id) -> Optional[str]:
    flow_state.set(KEY_RESTORE_SNAPSHOT_ID, meta.snapshot_id)
    flow_state.set(KEY_RESTORE_SNAPSHOT_NUMBER, meta.snapshot_number)
    flow_state.set(KEY_RESTORE_ERROR, None)
    return STATE_RESTORE_CONFIRM


handle_restore_list_input = build_text_input_handler(
    retry_state_id=STATE_RESTORE_LIST,
    resolve_matches=resolve_snapshot_number_matches,
    on_match_selected=_select_snapshot_from_match,
    invalid_number_message="❌ Please enter a positive snapshot number (e.g. `12`).",
    not_found_message="❌ Snapshot number **{number}** not found.",
    ambiguous_message="❌ Snapshot number **{number}** is ambiguous. Please use the buttons.",
)


async def build_restore_result_text(flow_state, api, user_id) -> str:
    snapshot_id = flow_state.get(KEY_RESTORE_SNAPSHOT_ID, "")
    err = flow_state.get(KEY_RESTORE_ERROR)

    if err:
        return (
            "❌ **Restore failed**\n\n"
            f"Snapshot: `{snapshot_id}`\n"
            f"Error: {err}"
        )

    return (
        "✅ **Snapshot restored**"
    )


async def build_clear_all_result_text(flow_state, api, user_id) -> str:
    err = flow_state.get(KEY_CLEAR_ALL_ERROR)
    result = flow_state.get(KEY_CLEAR_ALL_RESULT)

    if err:
        return "❌ **Clear failed**\n\n" f"Error: {err}"

    deleted_meta = str(result["deleted_meta"]) if result else "?"
    deleted_data = str(result["deleted_data"]) if result else "?"

    return (
        "✅ **All snapshots cleared**\n\n"
        f"Deleted meta docs: {deleted_meta}\n"
        f"Deleted data docs: {deleted_data}"
    )


async def build_clear_obsolete_result_text(flow_state, api, user_id) -> str:
    err = flow_state.get(KEY_CLEAR_OBSOLETE_ERROR)
    result = flow_state.get(KEY_CLEAR_OBSOLETE_RESULT)

    if err:
        return "❌ **Cleanup failed**\n\n" f"Error: {err}"

    deleted_meta = str(result["deleted_meta"]) if result else "?"
    deleted_data = str(result["deleted_data"]) if result else "?"

    return (
        "✅ **Obsolete snapshots deleted**\n\n"
        f"Deleted meta docs: {deleted_meta}\n"
        f"Deleted data docs: {deleted_data}"
    )


def create_snapshots_flow() -> MessageFlow:
    flow = MessageFlow()

    create_result_defaults = {
        KEY_CREATED_SNAPSHOT_ID: "",
        KEY_CREATED_SNAPSHOT_META: None,
    }
    restore_result_defaults = {
        KEY_RESTORE_ERROR: None,
    }
    clear_all_result_defaults = {
        KEY_CLEAR_ALL_ERROR: None,
        KEY_CLEAR_ALL_RESULT: None,
    }
    clear_obsolete_result_defaults = {
        KEY_CLEAR_OBSOLETE_ERROR: None,
        KEY_CLEAR_OBSOLETE_RESULT: None,
    }

    flow.add_state(
        MessageDefinition(
            state_id=STATE_MAIN,
            state_type=StateType.BUTTON,
            text_builder=build_main_text,
            keyboard_builder=build_main_keyboard,
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_CLEANUP_MENU,
            state_type=StateType.BUTTON,
            text_builder=build_cleanup_text,
            keyboard_builder=build_cleanup_keyboard,
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_CREATE_RESULT,
            state_type=StateType.BUTTON,
            text_builder=build_create_result_text,
            buttons=None,
            auto_exit_after_render=True,
            defaults=create_result_defaults,
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_RESTORE_LIST,
            state_type=StateType.MIXED,
            text_builder=build_restore_list_text,
            # 4-column grid, 3 rows per page => 12 items.
            pagination_config=PaginationConfig(page_size=12, items_per_row=4, close_button_text="◁ Back"),
            pagination_items_builder=list_snapshots,
            pagination_item_formatter=format_snapshot_line,
            pagination_item_button_builder=build_restore_button,
            exit_buttons=[],
            input_validator=RegexValidator(r"^\s*\d+\s*$", error_message="❌ Please type a snapshot number (e.g. `12`)."),
            on_input_received=handle_restore_list_input,
            next_state_map={CommonCallbacks.CLOSE: STATE_MAIN},
        )
    )

    flow.add_confirmation(
        state_id=STATE_CLEAR_ALL_CONFIRM_1,
        question=(
            "⚠️ **Delete ALL snapshots?**\n\n"
            "This will permanently delete all snapshot history."
        ),
        on_confirm_state=STATE_CLEAR_ALL_CONFIRM_2,
        on_cancel_state=STATE_CLEANUP_MENU,
        confirm_text="Continue",
        cancel_text="◁ Back",
        warning="This cannot be undone.",
    )

    flow.add_confirmation(
        state_id=STATE_CLEAR_ALL_CONFIRM_2,
        question=(
            "🚨 **Final confirmation**\n\n"
            "Really delete ALL snapshots?"
        ),
        on_confirm_state=STATE_CLEAR_ALL_RESULT,
        on_cancel_state=STATE_CLEANUP_MENU,
        confirm_text="✅ Yes, delete all",
        cancel_text="◁ Back",
        warning="This cannot be undone.",
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_CLEAR_ALL_RESULT,
            state_type=StateType.BUTTON,
            text_builder=build_clear_all_result_text,
            buttons=None,
            auto_exit_after_render=True,
            defaults=clear_all_result_defaults,
            on_enter=clear_all_snapshots,
        )
    )

    flow.add_confirmation(
        state_id=STATE_CLEAR_OBSOLETE_CONFIRM,
        question=(
            "Delete all **obsolete** snapshots?\n\n"
            "This deletes snapshots marked as obsolete and removes them from history."
        ),
        on_confirm_state=STATE_CLEAR_OBSOLETE_RESULT,
        on_cancel_state=STATE_CLEANUP_MENU,
        confirm_text="✅ Yes, delete obsolete",
        cancel_text="◁ Back",
        warning="This cannot be undone.",
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_CLEAR_OBSOLETE_RESULT,
            state_type=StateType.BUTTON,
            text_builder=build_clear_obsolete_result_text,
            buttons=None,
            auto_exit_after_render=True,
            defaults=clear_obsolete_result_defaults,
            on_enter=clear_obsolete_snapshots,
        )
    )

    flow.add_confirmation(
        state_id=STATE_RESTORE_CONFIRM,
        question=build_restore_confirmation,
        on_confirm_state=STATE_RESTORE_RESULT,
        on_cancel_state=STATE_RESTORE_LIST,
        confirm_text="✅ Restore",
        cancel_text="◁ Back",
        warning="⚠️ This will overwrite the current database state.",
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_RESTORE_RESULT,
            state_type=StateType.BUTTON,
            text_builder=build_restore_result_text,
            buttons=None,
            auto_exit_after_render=True,
            defaults=restore_result_defaults,
            on_enter=restore_selected,
        )
    )

    # Simple transitions
    flow.states[STATE_MAIN].next_state_map.update({CB_RESTORE: STATE_RESTORE_LIST, CB_CLEANUP: STATE_CLEANUP_MENU})
    flow.states[STATE_CLEANUP_MENU].next_state_map.update(
        {
            CommonCallbacks.BACK: STATE_MAIN,
            CB_CLEAR_ALL: STATE_CLEAR_ALL_CONFIRM_1,
            CB_CLEAR_OBSOLETE: STATE_CLEAR_OBSOLETE_CONFIRM,
        }
    )

    return flow
