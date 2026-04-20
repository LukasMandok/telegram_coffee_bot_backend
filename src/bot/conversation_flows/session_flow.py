"""Session flow (MessageFlow-based) for the group coffee order UI.

This replaces the legacy `group_selection` while-loop with a single MessageFlow state
that renders the group keyboard and routes callback buttons.

Design goals:
- Keep the group order UI in one editable message per user
- Preserve real-time sync via GroupKeyboardManager
- Handle per-user inactivity without affecting other participants
"""

from __future__ import annotations

from typing import Any, Optional

from ..message_flow import MessageAction, MessageDefinition, MessageFlow, StateType
from ..message_flow_ids import CommonCallbacks, CommonStateIds
from ...common.log import Logger
from ...exceptions.coffee_exceptions import CoffeeSessionError


_logger = Logger("SessionFlow")

STATE_SESSION_MAIN = "session_main"

KEY_SESSION_ID = "session_id"
KEY_SESSION_OBJ_ID = "session_obj_id"
KEY_IS_NEW_SESSION = "is_new_session"
KEY_REGISTERED = "registered"
KEY_EXIT_TEXT = "session_exit_text"

KEY_JOIN_STATUS_SENT = "join_status_sent"


async def _get_session(flow_state, api: Any):
    session_obj_id = flow_state.get(KEY_SESSION_OBJ_ID)
    if session_obj_id is None:
        return None

    return api.session_manager.get_session_by_id(str(session_obj_id))


async def _on_enter_session_main(flow_state, api: Any, user_id: int) -> None:
    if flow_state.get(KEY_IS_NEW_SESSION, True):
        return

    if flow_state.get(KEY_JOIN_STATUS_SENT, False):
        return

    session = await _get_session(flow_state, api)
    if session is None:
        return

    participant_count = 0
    try:
        participant_count = len(session.participants)
    except Exception:
        participant_count = 0

    participant_word = "participant" if participant_count == 1 else "participants"

    join_msg = await api.message_manager.send_text(
        int(user_id),
        "👥 **Joined existing coffee session!**\n"
        f"Session has {participant_count} {participant_word}",
        True,
        True,
    )

    try:
        if join_msg is not None and join_msg.id is not None:
            flow_state.add_aux_message(int(user_id), int(join_msg.id))
    except Exception:
        pass

    flow_state.set(KEY_JOIN_STATUS_SENT, True)


async def _build_session_text(flow_state, api: Any, user_id: int) -> str:
    session = await _get_session(flow_state, api)
    if session is None:
        return "❌ Session not found."

    _is_insufficient, _is_multi_card, message_text = await api.group_keyboard_manager._determine_flags_and_message(session)
    return message_text


async def _build_session_keyboard(flow_state, api: Any, user_id: int):
    session = await _get_session(flow_state, api)
    if session is None:
        return []

    is_insufficient, is_multi_card, _message_text = await api.group_keyboard_manager._determine_flags_and_message(session)

    session_id = str(session.id)
    current_page = 0
    active = api.group_keyboard_manager.active_keyboards.get(session_id, {}).get(int(user_id))
    if active is not None:
        current_page = int(active.current_page)

    return await api.group_keyboard_manager.create_group_keyboard(
        session.group_state,
        user_id,
        current_page,
        is_insufficient,
        is_multi_card,
    )


async def _on_render_register_keyboard(flow_state, api: Any, user_id: int) -> None:
    if flow_state.get(KEY_REGISTERED, False):
        return

    session = await _get_session(flow_state, api)
    if session is None or session.id is None:
        return

    if flow_state.current_message is None or flow_state.current_message.id is None:
        return

    session_id = str(session.id)
    try:
        await api.group_keyboard_manager.register_keyboard(
            int(user_id),
            int(flow_state.current_message.id),
            session_id,
            current_page=0,
        )
        flow_state.set(KEY_REGISTERED, True)
    except Exception as e:
        _logger.error("Failed to register session keyboard", exc=e)


async def _handle_inactivity(flow_state, api: Any, user_id: int) -> Optional[str]:
    session = await _get_session(flow_state, api)
    if session is None:
        flow_state.set(KEY_EXIT_TEXT, (flow_state.current_message.text if flow_state.current_message is not None else "") or "")
        return CommonStateIds.EXIT_CANCELLED

    # If the session already ended (e.g., submitted by another user), exit quietly.
    if not bool(session.is_active):
        flow_state.set(KEY_EXIT_TEXT, (flow_state.current_message.text if flow_state.current_message is not None else "") or "")
        return CommonStateIds.EXIT_CANCELLED

    # Per-user cleanup.
    try:
        api.group_keyboard_manager.unregister_keyboard(int(user_id), str(session.id))
    except Exception:
        pass

    try:
        await api.session_manager.remove_participant(int(user_id), session=session)
    except Exception:
        pass

    outcome = "unknown"
    try:
        outcome = await api.session_manager.cancel_or_delay_cancel_if_inactive(session)
    except Exception:
        outcome = "unknown"

    if outcome == "suspended":
        inactivity_text = (
            "⏱️ **Session suspended due to inactivity**\n\n"
            "Use /order to re-open the session."
        )
    elif outcome == "cancelled":
        inactivity_text = "⏱️ **Session cancelled due to inactivity**"
    elif outcome == "still_active":
        inactivity_text = (
            "⏱️ **Conversation closed due to inactivity**\n\n"
            "The session is still active. Use /order to join again."
        )
    else:
        inactivity_text = "⏱️ **Conversation closed due to inactivity**"

    flow_state.set(KEY_EXIT_TEXT, inactivity_text)
    return CommonStateIds.EXIT_CANCELLED


async def _on_button_press(data: str, flow_state, api: Any, user_id: int) -> Optional[str]:
    session = await _get_session(flow_state, api)
    if session is None:
        flow_state.set(KEY_EXIT_TEXT, "❌ Session not found.")
        return CommonStateIds.EXIT_CANCELLED

    try:
        await api.session_manager.mark_session_active(session)
    except Exception:
        pass

    if data == "group_submit":
        # Disable the keyboard immediately to prevent double-submits.
        try:
            if flow_state.current_message is not None:
                await api.message_manager.edit_message(flow_state.current_message, flow_state.current_message.text or "", buttons=None)
        except Exception:
            pass

        try:
            await api.session_manager.complete_session(int(user_id))
        except CoffeeSessionError:
            flow_state.set(KEY_EXIT_TEXT, "")
            return CommonStateIds.EXIT_CANCELLED
        except Exception as e:
            _logger.error("Session submit failed", exc=e)
            flow_state.set(KEY_EXIT_TEXT, "")
            return CommonStateIds.EXIT_CANCELLED

        try:
            api.group_keyboard_manager.unregister_keyboard(int(user_id), str(session.id))
        except Exception:
            pass

        # No extra "Submitted" message here; session completion notifications are sent
        # by SessionManager and the UI message gets cleaned up.
        flow_state.set(KEY_EXIT_TEXT, "")
        return CommonStateIds.EXIT_SUCCESS

    if data == "group_cancel":
        try:
            api.group_keyboard_manager.unregister_keyboard(int(user_id), str(session.id))
        except Exception:
            pass

        try:
            await api.session_manager.remove_participant(int(user_id), session=session)
        except Exception:
            pass

        outcome = "unknown"
        try:
            outcome = await api.session_manager.cancel_or_delay_cancel_if_inactive(session)
        except Exception:
            pass

        cancel_text = "❌ **Cancelled**"
        if outcome == "suspended":
            cancel_text = (
                "⏸️ **Session suspended**\n\n"
                "Use /order to re-open the session."
            )

        flow_state.set(KEY_EXIT_TEXT, cancel_text)
        return CommonStateIds.EXIT_CANCELLED

    if data.startswith("group_plus_"):
        name = data[len("group_plus_") :]
        await api.session_manager.update_session_member_coffee(name, "add")
        return None

    if data.startswith("group_minus_"):
        name = data[len("group_minus_") :]
        await api.session_manager.update_session_member_coffee(name, "remove")
        return None

    if data.startswith("group_reset_"):
        name = data[len("group_reset_") :]
        await api.group_keyboard_manager.handle_member_reset(session, name)
        return None

    if data == "group_show_archived":
        await api.group_keyboard_manager.handle_show_archived(session, int(user_id))
        return None

    if data in (CommonCallbacks.PAGE_NEXT, CommonCallbacks.PAGE_PREV):
        direction = "next" if data == CommonCallbacks.PAGE_NEXT else "prev"
        await api.group_keyboard_manager.handle_pagination(session, int(user_id), direction)
        return None

    # PAGE_INFO, group_info (member name), or unknown callbacks -> no-op
    return None


def create_session_flow(*, timeout_seconds: int = 180) -> MessageFlow:
    flow = MessageFlow()

    flow.add_state(
        MessageDefinition(
            state_id=STATE_SESSION_MAIN,
            state_type=StateType.BUTTON,
            text_builder=_build_session_text,
            keyboard_builder=_build_session_keyboard,
            action=MessageAction.AUTO,
            timeout=timeout_seconds,
            exit_buttons=[],
            on_enter=_on_enter_session_main,
            on_button_press=_on_button_press,
            on_timeout=_handle_inactivity,
            on_render=_on_render_register_keyboard,
        )
    )

    async def _build_exit_text(flow_state, api: Any, user_id: int) -> str:
        return str(flow_state.get(KEY_EXIT_TEXT, "") or "")

    flow.add_state(
        MessageDefinition(
            state_id=CommonStateIds.EXIT_CANCELLED,
            state_type=StateType.BUTTON,
            text_builder=_build_exit_text,
            buttons=None,
            action=MessageAction.AUTO,
            timeout=1,
            exit_buttons=[],
            auto_exit_after_render=True,
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=CommonStateIds.EXIT_SUCCESS,
            state_type=StateType.BUTTON,
            text_builder=_build_exit_text,
            buttons=None,
            action=MessageAction.AUTO,
            timeout=1,
            exit_buttons=[],
            auto_exit_after_render=True,
        )
    )

    return flow
