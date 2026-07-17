"""
Settings MessageFlow

Migrates the old conversation-based settings menus into a MessageFlow-driven
implementation. Each submenu is implemented as a MessageDefinition state with
handlers that read/update repository settings via `api.conversation_manager.repo`.

This file intentionally contains only conversation/UI behaviour; textual
content and keyboard generation remain in `SettingsUi`.
"""

from typing import List, Optional

from .custom_route_contracts import register_inline_custom_route, validate_schema_routes_registered
from .logging_modules_helpers import build_logging_modules_state_helpers
from ..message_flow import (
    ButtonCallback,
    MessageAction,
    MessageDefinition,
    MessageFlow,
    PaginationConfig,
    StateType,
)
from ..message_flow_helpers import (
    CommonCallbacks,
    ExitStateBuilder,
    NavigationButtons,
    apply_update_or_notify,
    make_state,
)
from ..settings_flow_generator import SettingsFlowGenerator
from ..settings_schema import ADMIN_MENU, CATEGORIES, MAIN_MENU
from ..settings_ui import SettingsUi


# State IDs
STATE_MAIN = "main"

STATE_ADMIN = "admin"
STATE_LOGGING_FORMAT = "logging_format"
STATE_LOGGING_MODULES = "logging_modules"
STATE_ADMIN_LOGGING_CATEGORY = "sd:cat:admin:logging"

STATE_SAVE_RESULT = "settings_saved"
STATE_REGISTRATION_PASSWORD = "registration_password"
STATE_REGISTRATION_PASSWORD_VERIFY = "registration_password_verify"
STATE_REGISTRATION_PASSWORD_NEW = "registration_password_new"


register_inline_custom_route(
    STATE_LOGGING_FORMAT,
    owner=__name__,
    summary="Inline logging format editor handled directly in settings_flow.py",
)
register_inline_custom_route(
    STATE_REGISTRATION_PASSWORD,
    owner=__name__,
    summary="Inline registration password submenu handled directly in settings_flow.py",
)


def _get_schema_custom_route_ids() -> list[str]:
    """Collect all custom_route_id values from the schema."""
    route_ids: list[str] = []
    for category in CATEGORIES.values():
        for setting in category.settings:
            if setting.custom_route_id:
                route_ids.append(setting.custom_route_id)
        for subcategory in getattr(category, "subcategories", []) or []:
            if subcategory.custom_route_id:
                route_ids.append(subcategory.custom_route_id)
    return route_ids


_SCHEMA_CUSTOM_ROUTES = _get_schema_custom_route_ids()
validate_schema_routes_registered(_SCHEMA_CUSTOM_ROUTES)


def create_settings_flow() -> MessageFlow:
    """Create the MessageFlow that implements the settings UI."""
    flow = MessageFlow()

    async def _user_update(flow_state, api, user_id: int, **kwargs) -> bool:
        repo = api.conversation_manager.repo
        before = await repo.get_user_settings(user_id)
        ok = await apply_update_or_notify(
            flow_state,
            api,
            user_id,
            repo.update_user_settings(user_id, **kwargs),
            clear_keys=["user_settings"],
        )
        return ok

    async def _global_update(flow_state, api, user_id: int, coro) -> bool:
        # `coro` is a coroutine returned by a repo.update_* call
        return await apply_update_or_notify(flow_state, api, user_id, coro)

    # Register schema-driven states (generic handlers for standard settings)
    # Provide parent mapping so category states use the correct back parent.
    SettingsFlowGenerator(
        user_update_handler=_user_update,
        global_update_handler=_global_update,
    ).register_schema_states(
        flow,
        parent_main=STATE_MAIN,
        parent_admin=STATE_ADMIN,
    )


    # ------------------ Main menu ------------------
    async def build_main_text(flow_state, api, user_id) -> str:
        sm = SettingsUi(api)
        return sm.get_main_menu_text()

    async def build_main_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
        repo = api.conversation_manager.repo
        is_admin = await repo.is_user_admin(user_id)
        sm = SettingsUi(api)
        return sm.get_main_menu_keyboard(include_admin=is_admin)

    async def handle_main_button(data: str, flow_state, api, user_id) -> Optional[str]:
        if data in MAIN_MENU.categories:
            return f"sd:cat:main:{data}"
        if data == "done":
            return STATE_SAVE_RESULT
        if data == "admin":
            is_admin = await api.conversation_manager.repo.is_user_admin(user_id)
            if not is_admin:
                await api.message_manager.send_text(
                    user_id,
                    "🔧 **Administration**\n\n❌ You need admin rights to access these settings.",
                    vanish=True,
                    conv=False,
                    delete_after=3,
                )
                return STATE_MAIN
            return STATE_ADMIN
        return None

    flow.add_state(make_state(
        STATE_MAIN,
        text_builder=build_main_text,
        keyboard_builder=build_main_keyboard,
        action=MessageAction.AUTO,
        timeout=180,
        on_button_press=handle_main_button,
        exit_buttons=[CommonCallbacks.CLOSE, CommonCallbacks.CANCEL],
    ))

    # ------------------ Admin root ------------------
    async def build_admin_text(flow_state, api, user_id) -> str:
        sm = SettingsUi(api)
        return sm.get_admin_submenu_text()

    async def build_admin_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
        sm = SettingsUi(api)
        return sm.get_admin_submenu_keyboard()

    async def handle_admin_button(data: str, flow_state, api, user_id) -> Optional[str]:
        # Support schema-driven category callbacks (sd:cat:<name>)
        if data and data.startswith("sd:cat:"):
            return data

        # Category callbacks from schema-defined admin menu.
        if data in ADMIN_MENU.categories:
            return f"sd:cat:admin:{data}"
        if data == "registration_password":
            return STATE_REGISTRATION_PASSWORD
        return None

    flow.add_state(make_state(
        STATE_ADMIN,
        text_builder=build_admin_text,
        keyboard_builder=build_admin_keyboard,
        action=MessageAction.EDIT,
        timeout=180,
        on_button_press=handle_admin_button,
        back_button=CommonCallbacks.BACK,
        parent_state=STATE_MAIN,
    ))

    # ------------------ Admin: Registration Password ------------------
    async def build_registration_password_text(flow_state, api, user_id) -> str:
        sm = SettingsUi(api)
        return sm.get_registration_password_submenu_text()

    async def build_registration_password_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
        sm = SettingsUi(api)
        return sm.get_registration_password_submenu_keyboard()

    async def handle_registration_password_button(data: str, flow_state, api, user_id) -> Optional[str]:
        if data == "change_password":
            # reset tries counter
            flow_state.set("reg_change_pw_tries", 0)
            return STATE_REGISTRATION_PASSWORD_VERIFY
        return None

    flow.add_state(make_state(
        STATE_REGISTRATION_PASSWORD,
        text_builder=build_registration_password_text,
        keyboard_builder=build_registration_password_keyboard,
        action=MessageAction.EDIT,
        timeout=120,
        on_button_press=handle_registration_password_button,
        back_button=CommonCallbacks.BACK,
        parent_state=STATE_ADMIN,
    ))

    async def build_registration_password_verify_text(flow_state, api, user_id) -> str:
        return (
            "🔐 **Change Registration Password**\n\n"
            "The registration password is needed to register as a new user.\n\n"
            "Enter the current registration password:"
        )

    async def handle_registration_password_verify_input(input_text: str, flow_state, api, user_id) -> Optional[str]:
        repo = api.conversation_manager.repo
        pw_doc = await repo.get_password()
        if pw_doc is None:
            await api.message_manager.send_text(user_id, "❌ Registration password not configured.", vanish=True, conv=False, delete_after=3)
            return STATE_ADMIN

        if not pw_doc.verify_password((input_text or "").strip()):
            tries = int(flow_state.get("reg_change_pw_tries", 0)) + 1
            flow_state.set("reg_change_pw_tries", tries)
            if tries < 3:
                await api.message_manager.send_text(user_id, f"❌ Password incorrect. Please try again. ({tries}/3 attempts used)", vanish=True, conv=True, delete_after=4)
                return STATE_REGISTRATION_PASSWORD_VERIFY
            await api.message_manager.send_text(user_id, "❌ Too many incorrect attempts. Aborting.", True, True)
            return STATE_ADMIN

        # Verified — proceed to ask for new password
        flow_state.clear("reg_change_pw_tries")
        return STATE_REGISTRATION_PASSWORD_NEW

    flow.add_state(MessageDefinition(
        state_id=STATE_REGISTRATION_PASSWORD_VERIFY,
        state_type=StateType.TEXT_INPUT,
        text_builder=build_registration_password_verify_text,
        input_storage_key=STATE_REGISTRATION_PASSWORD_VERIFY,
        on_input_received=handle_registration_password_verify_input,
        action=MessageAction.EDIT,
        back_button=CommonCallbacks.BACK,
        # Provide explicit buttons so users can cancel via keyboard while typing
        buttons=[NavigationButtons.back(), NavigationButtons.cancel()],
        parent_state=STATE_REGISTRATION_PASSWORD,
    ))

    async def build_registration_password_new_text(flow_state, api, user_id) -> str:
        return (
            "🔑 **Set New Registration Password**\n\n"
            "Enter the new registration password (min 4 characters):"
        )

    async def handle_registration_password_new_input(input_text: str, flow_state, api, user_id) -> Optional[str]:
        new_pw = (input_text or "").strip()
        if not new_pw or len(new_pw) < 4:
            await api.message_manager.send_text(user_id, "❌ Password too short. Enter at least 4 characters.", vanish=True, conv=True, delete_after=3)
            return STATE_REGISTRATION_PASSWORD_NEW
        ok = await _global_update(flow_state, api, user_id, api.conversation_manager.repo.update_password(new_pw))
        if not ok:
            return STATE_REGISTRATION_PASSWORD_NEW
        await api.message_manager.send_text(user_id, "✅ Registration password updated.", True, True, delete_after=5)
        return STATE_ADMIN

    flow.add_state(MessageDefinition(
        state_id=STATE_REGISTRATION_PASSWORD_NEW,
        state_type=StateType.TEXT_INPUT,
        text_builder=build_registration_password_new_text,
        input_storage_key=STATE_REGISTRATION_PASSWORD_NEW,
        on_input_received=handle_registration_password_new_input,
        action=MessageAction.EDIT,
        back_button=CommonCallbacks.BACK,
        # Allow cancelling the input with an explicit Cancel button
        buttons=[NavigationButtons.back(), NavigationButtons.cancel()],
        parent_state=STATE_REGISTRATION_PASSWORD,
    ))

    # Logging format toggles
    async def build_logging_format_text(flow_state, api, user_id) -> str:
        repo = api.conversation_manager.repo
        log_settings = await repo.get_log_settings() or {}
        sm = SettingsUi(api)
        return sm.get_logging_format_text(log_settings)

    async def build_logging_format_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
        repo = api.conversation_manager.repo
        log_settings = await repo.get_log_settings() or {}
        sm = SettingsUi(api)
        return sm.get_logging_format_keyboard(log_settings)

    async def handle_logging_format_button(data: str, flow_state, api, user_id) -> Optional[str]:
        repo = api.conversation_manager.repo
        log_settings = await repo.get_log_settings() or {}
        if data == "toggle_time":
            new_value = not log_settings.get("log_show_time", True)
            await _global_update(flow_state, api, user_id, repo.update_log_settings(log_show_time=new_value))
            return STATE_LOGGING_FORMAT
        if data == "toggle_caller":
            new_value = not log_settings.get("log_show_caller", True)
            await _global_update(flow_state, api, user_id, repo.update_log_settings(log_show_caller=new_value))
            return STATE_LOGGING_FORMAT
        if data == "toggle_class":
            new_value = not log_settings.get("log_show_class", True)
            await _global_update(flow_state, api, user_id, repo.update_log_settings(log_show_class=new_value))
            return STATE_LOGGING_FORMAT
        return None

    flow.add_state(make_state(
        STATE_LOGGING_FORMAT,
        text_builder=build_logging_format_text,
        keyboard_builder=build_logging_format_keyboard,
        action=MessageAction.EDIT,
        timeout=120,
        on_button_press=handle_logging_format_button,
        back_button=CommonCallbacks.BACK,
        parent_state=STATE_ADMIN_LOGGING_CATEGORY,
    ))

    # Logging modules editor (paginated) — isolated in dedicated helper module.
    (
        pagination_items_builder,
        pagination_item_button_builder,
        pagination_extras,
        build_logging_modules_text,
        handle_logging_modules_button,
    ) = build_logging_modules_state_helpers(
        state_logging_modules=STATE_LOGGING_MODULES,
        state_logging=STATE_ADMIN_LOGGING_CATEGORY,
    )

    flow.add_state(MessageDefinition(
        state_id=STATE_LOGGING_MODULES,
        text_builder=build_logging_modules_text,
        pagination_config=PaginationConfig(page_size=18, items_per_row=2, show_page_numbers=True, show_close_button=False, extras_position="below"),
        pagination_items_builder=pagination_items_builder,
        pagination_item_button_builder=pagination_item_button_builder,
        pagination_extra_buttons_builder=pagination_extras,
        action=MessageAction.EDIT,
        timeout=180,
        on_button_press=handle_logging_modules_button,
        back_button=CommonCallbacks.BACK,
        parent_state=STATE_ADMIN_LOGGING_CATEGORY,
    ))

    # Short success exit state used when user presses 'Done' in main settings
    flow.add_state(ExitStateBuilder.create(state_id=STATE_SAVE_RESULT, text="✅ Settings saved!", timeout=1, delete_after=0))

    return flow

