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

from ..message_flow import MessageAction, MessageFlow
from ..message_flow_helpers import make_state
from ...common.log import Logger
from ...exceptions.coffee_exceptions import CoffeeSessionError


_logger = Logger("SessionFlow")

STATE_SESSION_MAIN = "session_main"
STATE_SESSION_EXIT_SUCCESS = "session_exit_success"

KEY_SESSION_ID = "session_id"
KEY_SESSION_OBJ_ID = "session_obj_id"
KEY_REGISTERED = "registered"


async def _get_session(flow_state, api: Any):
    session_obj_id = flow_state.get(KEY_SESSION_OBJ_ID)
    if session_obj_id is None:
        return None

    return api.session_manager.get_session_by_id(str(session_obj_id))


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
        return "__exit__"

    # If the session already ended (e.g., submitted by another user), exit quietly.
    if not bool(session.is_active):
        return "__exit__"

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

    try:
        if flow_state.current_message is not None:
            await api.message_manager.edit_message(flow_state.current_message, inactivity_text, buttons=None)
    except Exception:
        pass

    return "__exit__"


async def _on_button_press(data: str, flow_state, api: Any, user_id: int) -> Optional[str]:
    session = await _get_session(flow_state, api)
    if session is None:
        return "__exit__"

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
            return "__exit__"
        except Exception as e:
            _logger.error("Session submit failed", exc=e)
            return "__exit__"

        try:
            api.group_keyboard_manager.unregister_keyboard(int(user_id), str(session.id))
        except Exception:
            pass

        return STATE_SESSION_EXIT_SUCCESS

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

        try:
            if flow_state.current_message is not None:
                await api.message_manager.edit_message(flow_state.current_message, cancel_text, buttons=None)
        except Exception:
            pass

        return "__exit__"

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

    if data in ("group_next", "group_prev"):
        direction = "next" if data == "group_next" else "prev"
        await api.group_keyboard_manager.handle_pagination(session, int(user_id), direction)
        return None

    # group_info or unknown callbacks -> no-op
    return None


def create_session_flow(*, timeout_seconds: int = 180) -> MessageFlow:
    flow = MessageFlow()

    flow.add_state(
        make_state(
            STATE_SESSION_MAIN,
            text_builder=_build_session_text,
            keyboard_builder=_build_session_keyboard,
            action=MessageAction.AUTO,
            timeout=timeout_seconds,
            exit_buttons=[],
            on_button_press=_on_button_press,
            on_timeout=_handle_inactivity,
            on_render=_on_render_register_keyboard,
        )
    )

    # Exit state used to end the flow with `True` after successful submit.
    # We keep the message unchanged (text='') and return immediately.
    flow.add_state(
        make_state(
            STATE_SESSION_EXIT_SUCCESS,
            text="",
            buttons=None,
            action=MessageAction.AUTO,
            timeout=1,
            exit_buttons=[],
        )
    )

    return flow
