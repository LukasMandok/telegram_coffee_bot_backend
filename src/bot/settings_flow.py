"""
Settings MessageFlow

Migrates the old conversation-based settings menus into a MessageFlow-driven
implementation. Each submenu is implemented as a MessageDefinition state with
handlers that read/update repository settings via `api.conversation_manager.repo`.

This file intentionally contains only conversation/UI behaviour; textual
content and keyboard generation remain in `SettingsManager`.
"""

from typing import Any, Callable, Dict, List, Optional

from ..common.log import get_known_loggers, LOG_STATE_ICON, LOG_STATE_SEQUENCE, format_log_state
from .message_flow import (
    MessageFlow,
    MessageDefinition,
    ButtonCallback,
    MessageAction,
    StateType,
    PaginationConfig,
)
from .message_flow_helpers import (
    make_state,
    NavigationButtons,
    toggle_button,
    ExitStateBuilder,
    IntegerParser,
    CommonCallbacks,
    CommonStateIds,
)
from .settings_manager import SettingsManager


# State IDs
STATE_MAIN = "main"
STATE_ORDERING = "ordering"
STATE_PAGE_SIZE = "page_size"
STATE_SORTING = "sorting"
STATE_VANISHING = "vanishing"
STATE_VANISHING_THRESHOLD = "vanishing_threshold"
STATE_USER_NOTIFICATIONS = "user_notifications"

STATE_ADMIN = "admin"
STATE_LOGGING = "logging"
STATE_LOGGING_FORMAT = "logging_format"
STATE_LOGGING_MODULES = "logging_modules"
STATE_LOG_LEVEL = "log_level"

STATE_NOTIFICATIONS = "notifications"
STATE_DEBTS = "debts"
STATE_DEBT_METHOD = "debt_method"
STATE_DEBT_THRESHOLD = "debt_threshold"

STATE_GSHEET = "gsheet"
STATE_GSHEET_SET_PERIOD = "gsheet_set_period"

STATE_SNAPSHOTS = "snapshots"
STATE_SNAPSHOT_SET_KEEP_LAST = "snapshots_set_keep_last"
STATE_SNAPSHOT_CREATION_POINTS = "snapshots_creation_points"
STATE_SAVE_RESULT = "settings_saved"


def create_settings_flow() -> MessageFlow:
    """Create the MessageFlow that implements the settings UI."""
    flow = MessageFlow()

    # ------------------ Main menu ------------------
    async def build_main_text(flow_state, api, user_id) -> str:
        sm = SettingsManager(api)
        return sm.get_main_menu_text()

    async def build_main_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
        repo = api.conversation_manager.repo
        is_admin = await repo.is_user_admin(user_id)
        sm = SettingsManager(api)
        return sm.get_main_menu_keyboard(include_admin=is_admin)

    async def handle_main_button(data: str, flow_state, api, user_id) -> Optional[str]:
        if data == "ordering":
            return STATE_ORDERING
        if data == "vanishing":
            return STATE_VANISHING
        if data == "user_notifications":
            return STATE_USER_NOTIFICATIONS
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

    # ------------------ Ordering ------------------
    async def build_ordering_text(flow_state, api, user_id) -> str:
        user_settings = await flow_state.get_or_fetch(
            "user_settings", lambda: api.conversation_manager.repo.get_user_settings(user_id)
        )
        sm = SettingsManager(api)
        return sm.get_ordering_submenu_text(user_settings)

    async def build_ordering_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
        sm = SettingsManager(api)
        return sm.get_ordering_submenu_keyboard()

    async def handle_ordering_button(data: str, flow_state, api, user_id) -> Optional[str]:
        if data == "page_size":
            return STATE_PAGE_SIZE
        if data == "sorting":
            return STATE_SORTING
        return None

    flow.add_state(make_state(
        STATE_ORDERING,
        text_builder=build_ordering_text,
        keyboard_builder=build_ordering_keyboard,
        action=MessageAction.EDIT,
        timeout=120,
        on_button_press=handle_ordering_button,
        back_button=CommonCallbacks.BACK,
        parent_state=STATE_MAIN,
    ))

    # Page size (text input)
    async def build_page_size_text(flow_state, api, user_id) -> str:
        settings = await flow_state.get_or_fetch("user_settings", lambda: api.conversation_manager.repo.get_user_settings(user_id))
        return (
            "🔢 **Group Page Size**\n\n"
            f"Current value: {settings.group_page_size}\n\n"
            "Enter a new page size (5-20):"
        )

    async def handle_page_size_input(input_text: str, flow_state, api, user_id) -> Optional[str]:
        parser = IntegerParser()
        val = parser.parse(input_text)
        if val is None or val < 5 or val > 20:
            await api.message_manager.send_text(
                user_id,
                "❌ Invalid input. Please enter a number between 5 and 20.",
                vanish=True,
                conv=True,
                delete_after=3,
            )
            return STATE_PAGE_SIZE

        success = await api.conversation_manager.repo.update_user_settings(user_id, group_page_size=val)
        if not success:
            await api.message_manager.send_text(
                user_id,
                "❌ Failed to save settings. Please try again.",
                vanish=True,
                conv=False,
                delete_after=3,
            )
            return STATE_PAGE_SIZE

        # Invalidate cached user_settings so parent will re-fetch
        flow_state.clear("user_settings")
        return STATE_ORDERING

    flow.add_state(MessageDefinition(
        state_id=STATE_PAGE_SIZE,
        state_type=StateType.TEXT_INPUT,
        text_builder=build_page_size_text,
        input_prompt=None,
        input_storage_key=STATE_PAGE_SIZE,
        input_timeout=120,
        on_input_received=handle_page_size_input,
        action=MessageAction.EDIT,
        back_button=CommonCallbacks.BACK,
        parent_state=STATE_ORDERING,
    ))

    # Sorting options
    async def build_sorting_text(flow_state, api, user_id) -> str:
        settings = await flow_state.get_or_fetch("user_settings", lambda: api.conversation_manager.repo.get_user_settings(user_id))
        sm = SettingsManager(api)
        return sm.get_sorting_options_text(settings)

    async def build_sorting_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
        sm = SettingsManager(api)
        return sm.get_sorting_options_keyboard()

    async def handle_sorting_button(data: str, flow_state, api, user_id) -> Optional[str]:
        if data not in ("alphabetical", "coffee_count"):
            return None
        success = await api.conversation_manager.repo.update_user_settings(user_id, group_sort_by=data)
        if not success:
            await api.message_manager.send_text(user_id, "❌ Failed to update settings", vanish=True, conv=False, delete_after=3)
            return STATE_SORTING
        flow_state.clear("user_settings")
        return STATE_ORDERING

    flow.add_state(make_state(
        STATE_SORTING,
        text_builder=build_sorting_text,
        keyboard_builder=build_sorting_keyboard,
        action=MessageAction.EDIT,
        timeout=60,
        on_button_press=handle_sorting_button,
        back_button=CommonCallbacks.BACK,
        parent_state=STATE_ORDERING,
    ))

    # ------------------ Vanishing ------------------
    async def build_vanishing_text(flow_state, api, user_id) -> str:
        settings = await flow_state.get_or_fetch("user_settings", lambda: api.conversation_manager.repo.get_user_settings(user_id))
        sm = SettingsManager(api)
        return sm.get_vanishing_submenu_text(settings)

    async def build_vanishing_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
        settings = await flow_state.get_or_fetch("user_settings", lambda: api.conversation_manager.repo.get_user_settings(user_id))
        sm = SettingsManager(api)
        return sm.get_vanishing_submenu_keyboard(settings)

    async def handle_vanishing_button(data: str, flow_state, api, user_id) -> Optional[str]:
        settings = await api.conversation_manager.repo.get_user_settings(user_id)
        if data == "toggle":
            new_value = not bool(getattr(settings, "vanishing_enabled", False))
            success = await api.conversation_manager.repo.update_user_settings(user_id, vanishing_enabled=new_value)
            if not success:
                await api.message_manager.send_text(user_id, "❌ Failed to update settings", vanish=True, conv=False, delete_after=3)
                return STATE_VANISHING
            flow_state.clear("user_settings")
            return STATE_VANISHING
        if data == "threshold":
            return STATE_VANISHING_THRESHOLD
        return None

    flow.add_state(make_state(
        STATE_VANISHING,
        text_builder=build_vanishing_text,
        keyboard_builder=build_vanishing_keyboard,
        action=MessageAction.EDIT,
        timeout=120,
        on_button_press=handle_vanishing_button,
        back_button=CommonCallbacks.BACK,
        parent_state=STATE_MAIN,
    ))

    # Vanishing threshold input
    async def build_vanish_threshold_text(flow_state, api, user_id) -> str:
        settings = await flow_state.get_or_fetch("user_settings", lambda: api.conversation_manager.repo.get_user_settings(user_id))
        return (
            "🔢 **Vanishing Threshold**\n\n"
            f"Current value: {settings.vanishing_threshold}\n\n"
            "Enter a new threshold (1-10):"
        )

    async def handle_vanish_threshold_input(input_text: str, flow_state, api, user_id) -> Optional[str]:
        parser = IntegerParser()
        val = parser.parse(input_text)
        if val is None or val < 1 or val > 10:
            await api.message_manager.send_text(user_id, "❌ Invalid input. Please enter a number between 1 and 10.", vanish=True, conv=True, delete_after=3)
            return STATE_VANISHING_THRESHOLD
        success = await api.conversation_manager.repo.update_user_settings(user_id, vanishing_threshold=val)
        if not success:
            await api.message_manager.send_text(user_id, "❌ Failed to save settings. Please try again.", vanish=True, conv=False, delete_after=3)
            return STATE_VANISHING_THRESHOLD
        flow_state.clear("user_settings")
        return STATE_VANISHING

    flow.add_state(MessageDefinition(
        state_id=STATE_VANISHING_THRESHOLD,
        state_type=StateType.TEXT_INPUT,
        text_builder=build_vanish_threshold_text,
        input_storage_key=STATE_VANISHING_THRESHOLD,
        on_input_received=handle_vanish_threshold_input,
        action=MessageAction.EDIT,
        back_button=CommonCallbacks.BACK,
        parent_state=STATE_VANISHING,
    ))

    # ------------------ User notifications ------------------
    async def build_user_notifications_text(flow_state, api, user_id) -> str:
        user_settings = await flow_state.get_or_fetch("user_settings", lambda: api.conversation_manager.repo.get_user_settings(user_id))
        notification_settings = await api.conversation_manager.repo.get_notification_settings() or {"notifications_enabled": True, "notifications_silent": False}
        sm = SettingsManager(api)
        return sm.get_user_notifications_submenu_text(user_settings, notification_settings)

    async def build_user_notifications_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
        user_settings = await flow_state.get_or_fetch("user_settings", lambda: api.conversation_manager.repo.get_user_settings(user_id))
        notification_settings = await api.conversation_manager.repo.get_notification_settings() or {"notifications_enabled": True, "notifications_silent": False}
        sm = SettingsManager(api)
        return sm.get_user_notifications_submenu_keyboard(user_settings, notification_settings)

    async def handle_user_notifications_button(data: str, flow_state, api, user_id) -> Optional[str]:
        user_settings = await api.conversation_manager.repo.get_user_settings(user_id)
        if data == "toggle_user_notifications":
            new_value = not bool(getattr(user_settings, "notifications_enabled", True))
            updated = await api.conversation_manager.repo.update_user_settings(user_id, notifications_enabled=new_value)
            if not updated:
                await api.message_manager.send_text(user_id, "❌ Failed to update settings", vanish=True, conv=False, delete_after=3)
                return STATE_USER_NOTIFICATIONS
            flow_state.clear("user_settings")
            return STATE_USER_NOTIFICATIONS

        if data == "toggle_user_silent":
            new_value = not bool(getattr(user_settings, "notifications_silent", False))
            updated = await api.conversation_manager.repo.update_user_settings(user_id, notifications_silent=new_value)
            if not updated:
                await api.message_manager.send_text(user_id, "❌ Failed to update settings", vanish=True, conv=False, delete_after=3)
                return STATE_USER_NOTIFICATIONS
            flow_state.clear("user_settings")
            return STATE_USER_NOTIFICATIONS

        return None

    flow.add_state(make_state(
        STATE_USER_NOTIFICATIONS,
        text_builder=build_user_notifications_text,
        keyboard_builder=build_user_notifications_keyboard,
        action=MessageAction.EDIT,
        timeout=120,
        on_button_press=handle_user_notifications_button,
        back_button=CommonCallbacks.BACK,
        parent_state=STATE_MAIN,
    ))

    # ------------------ Admin root ------------------
    async def build_admin_text(flow_state, api, user_id) -> str:
        sm = SettingsManager(api)
        return sm.get_admin_submenu_text()

    async def build_admin_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
        sm = SettingsManager(api)
        return sm.get_admin_submenu_keyboard()

    async def handle_admin_button(data: str, flow_state, api, user_id) -> Optional[str]:
        if data == "logging":
            return STATE_LOGGING
        if data == "notifications":
            return STATE_NOTIFICATIONS
        if data == "debts":
            return STATE_DEBTS
        if data == "gsheet":
            return STATE_GSHEET
        if data == "snapshots":
            return STATE_SNAPSHOTS
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

    # ------------------ Logging submenu ------------------
    async def build_logging_text(flow_state, api, user_id) -> str:
        repo = api.conversation_manager.repo
        log_settings = await repo.get_log_settings() or {}
        sm = SettingsManager(api)
        return sm.get_logging_submenu_text(log_settings)

    async def build_logging_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
        sm = SettingsManager(api)
        return sm.get_logging_submenu_keyboard()

    async def handle_logging_button(data: str, flow_state, api, user_id) -> Optional[str]:
        if data == "log_level":
            return STATE_LOG_LEVEL
        if data == "log_format":
            return STATE_LOGGING_FORMAT
        if data == "log_modules":
            return STATE_LOGGING_MODULES
        return None

    flow.add_state(make_state(
        STATE_LOGGING,
        text_builder=build_logging_text,
        keyboard_builder=build_logging_keyboard,
        action=MessageAction.EDIT,
        timeout=120,
        on_button_press=handle_logging_button,
        back_button=CommonCallbacks.BACK,
        parent_state=STATE_ADMIN,
    ))

    # Logging format toggles
    async def build_logging_format_text(flow_state, api, user_id) -> str:
        repo = api.conversation_manager.repo
        log_settings = await repo.get_log_settings() or {}
        sm = SettingsManager(api)
        return sm.get_logging_format_text(log_settings)

    async def build_logging_format_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
        repo = api.conversation_manager.repo
        log_settings = await repo.get_log_settings() or {}
        sm = SettingsManager(api)
        return sm.get_logging_format_keyboard(log_settings)

    async def handle_logging_format_button(data: str, flow_state, api, user_id) -> Optional[str]:
        repo = api.conversation_manager.repo
        log_settings = await repo.get_log_settings() or {}
        if data == "toggle_time":
            new_value = not log_settings.get("log_show_time", True)
            success = await repo.update_log_settings(log_show_time=new_value)
            if not success:
                await api.message_manager.send_text(user_id, "❌ Failed to update settings", vanish=True, conv=False, delete_after=3)
            return STATE_LOGGING_FORMAT
        if data == "toggle_caller":
            new_value = not log_settings.get("log_show_caller", True)
            success = await repo.update_log_settings(log_show_caller=new_value)
            if not success:
                await api.message_manager.send_text(user_id, "❌ Failed to update settings", vanish=True, conv=False, delete_after=3)
            return STATE_LOGGING_FORMAT
        if data == "toggle_class":
            new_value = not log_settings.get("log_show_class", True)
            success = await repo.update_log_settings(log_show_class=new_value)
            if not success:
                await api.message_manager.send_text(user_id, "❌ Failed to update settings", vanish=True, conv=False, delete_after=3)
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
        parent_state=STATE_LOGGING,
    ))

    # Log level selection
    async def build_log_level_text(flow_state, api, user_id) -> str:
        repo = api.conversation_manager.repo
        log_settings = await repo.get_log_settings() or {}
        sm = SettingsManager(api)
        return sm.get_log_level_options_text(log_settings.get("log_level", "INFO"))

    async def build_log_level_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
        sm = SettingsManager(api)
        return sm.get_log_level_options_keyboard()

    async def handle_log_level_button(data: str, flow_state, api, user_id) -> Optional[str]:
        repo = api.conversation_manager.repo
        if not data:
            return None
        success = await repo.update_log_settings(log_level=data)
        if not success:
            await api.message_manager.send_text(user_id, "❌ Failed to update settings", vanish=True, conv=False, delete_after=3)
            return STATE_LOG_LEVEL
        return STATE_LOGGING

    flow.add_state(make_state(
        STATE_LOG_LEVEL,
        text_builder=build_log_level_text,
        keyboard_builder=build_log_level_keyboard,
        action=MessageAction.EDIT,
        timeout=60,
        on_button_press=handle_log_level_button,
        back_button=CommonCallbacks.BACK,
        parent_state=STATE_LOGGING,
    ))

    # Logging modules editor (paginated)
    def _make_module_button(module_name: str, display: str, idx: int, current_state_getter: Callable[[], str]):
        label = display
        callback = f"m_{idx}"

        async def _handler(flow_state, api, user_id) -> Optional[str]:
            # Toggle staging for this module
            pending: Dict[str, str] = flow_state.get("logging_pending_overrides", {}) or {}
            db_overrides = (await api.conversation_manager.repo.get_log_settings()) or {}
            existing = dict(db_overrides.get("log_module_overrides", {}) or {})
            current_state = flow_state.get("logging_current_state_index")
            if current_state is None:
                # Initialize
                cur_level = (db_overrides.get("log_level") or "INFO").upper()
                flow_state.set("logging_current_state_index", LOG_STATE_SEQUENCE.index(cur_level) if cur_level in LOG_STATE_SEQUENCE else 2)
                current_state = flow_state.get("logging_current_state_index")

            cur_idx = int(flow_state.get("logging_current_state_index") or 0)
            target_level = LOG_STATE_SEQUENCE[cur_idx]

            if module_name in pending:
                pending.pop(module_name, None)
            else:
                pending[module_name] = target_level

            flow_state.set("logging_pending_overrides", pending)
            # Refresh cached pagination items so the button labels update immediately
            try:
                if STATE_LOGGING_MODULES in flow_state.pagination_state:
                    new_items = await pagination_items_builder(flow_state, api, user_id)
                    flow_state.pagination_state[STATE_LOGGING_MODULES]["items"] = new_items
            except Exception:
                # Best-effort refresh; ignore errors so UI still functions
                pass
            return STATE_LOGGING_MODULES

        return ButtonCallback(f"{label}", callback, callback_handler=_handler)

    async def pagination_items_builder(flow_state, api, user_id):
        # Build list of (module_name, label) where label includes current state icon
        repo = api.conversation_manager.repo
        db_log_settings = await repo.get_log_settings() or {}
        global_level = (db_log_settings.get("log_level") or "INFO").upper()
        if global_level not in LOG_STATE_SEQUENCE:
            global_level = "INFO"

        pending = flow_state.get("logging_pending_overrides") or {}
        existing_overrides = dict(db_log_settings.get("log_module_overrides", {}) or {})

        items: list[tuple[str, str]] = []
        for module_name, display in get_known_loggers():
            if module_name in pending:
                state = pending[module_name]
                suffix = " *"
            elif module_name in existing_overrides:
                state = existing_overrides[module_name]
                suffix = ""
            else:
                state = global_level
                suffix = ""

            icon = LOG_STATE_ICON.get(state, "")
            label = f"{display} {icon}{suffix}".strip()
            items.append((module_name, label))

        return items

    def pagination_item_button_builder(item, idx):
        module_name, label = item
        # Use a simple string getter; actual handler closure created above will access flow_state
        return _make_module_button(module_name, label, idx, lambda: "")

    async def pagination_extras(flow_state, api, user_id) -> List[List[ButtonCallback]]:
        # Build state selector and apply/back row
        db_log_settings = await api.conversation_manager.repo.get_log_settings() or {}
        global_level = (db_log_settings.get("log_level") or "INFO").upper()
        current_state_index = flow_state.get("logging_current_state_index")
        if current_state_index is None:
            current_state_index = LOG_STATE_SEQUENCE.index(global_level) if global_level in LOG_STATE_SEQUENCE else 2
            flow_state.set("logging_current_state_index", current_state_index)

        current_state = LOG_STATE_SEQUENCE[int(flow_state.get("logging_current_state_index"))]
        current_icon = LOG_STATE_ICON.get(current_state, "")

        header = [
            ButtonCallback("⬅", "state_prev"),
            ButtonCallback(f"{current_state} {current_icon}", "noop"),
            ButtonCallback("➡", "state_next"),
        ]

        footer = [
            ButtonCallback("✅ Apply", "apply"),
            ButtonCallback(f"{SettingsManager.ICON_BACK} Back", CommonCallbacks.BACK),
        ]

        return [header, footer]

    async def build_logging_modules_text(flow_state, api, user_id) -> str:
        db_log_settings = await api.conversation_manager.repo.get_log_settings() or {}
        global_level = (db_log_settings.get("log_level") or "INFO").upper()
        current_index = flow_state.get("logging_current_state_index")
        if current_index is None:
            current_index = LOG_STATE_SEQUENCE.index(global_level) if global_level in LOG_STATE_SEQUENCE else 2
            flow_state.set("logging_current_state_index", current_index)

        pending = flow_state.get("logging_pending_overrides") or {}
        lines = [
            "🧩 **Module Logging**",
            "",
            f"**Global level:** {format_log_state(global_level)}",
            f"**Pending changes:** {len(pending)}",
            "",
            "Tap a module to stage that apply state. Tap it again to revert the staged change.",
        ]

        return "\n".join(lines)

    async def handle_logging_modules_button(data: str, flow_state, api, user_id) -> Optional[str]:
        if data is None:
            return None
        if data == "state_prev":
            idx = int(flow_state.get("logging_current_state_index") or 0)
            idx = (idx + 1) % len(LOG_STATE_SEQUENCE)
            flow_state.set("logging_current_state_index", idx)
            return STATE_LOGGING_MODULES
        if data == "state_next":
            idx = int(flow_state.get("logging_current_state_index") or 0)
            idx = (idx - 1) % len(LOG_STATE_SEQUENCE)
            flow_state.set("logging_current_state_index", idx)
            return STATE_LOGGING_MODULES
        if data == "apply":
            pending = flow_state.get("logging_pending_overrides") or {}
            db_log_settings = await api.conversation_manager.repo.get_log_settings() or {}
            existing_overrides = dict(db_log_settings.get("log_module_overrides", {}) or {})
            new_overrides = dict(existing_overrides)
            for k, v in (pending or {}).items():
                new_overrides[k] = v
            success = await api.conversation_manager.repo.update_log_settings(log_module_overrides=new_overrides)
            if not success:
                await api.message_manager.send_text(user_id, "❌ Failed to apply module logging settings.", vanish=True, conv=False, delete_after=3)
                return STATE_LOGGING_MODULES
            # clear staging
            flow_state.set("logging_pending_overrides", {})
            return STATE_LOGGING
        if data == "noop":
            return STATE_LOGGING_MODULES

        # module toggle callbacks are handled by per-button callback_handler above
        return None

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
        parent_state=STATE_LOGGING,
    ))

    # ------------------ Admin: Notifications ------------------
    async def build_notifications_text(flow_state, api, user_id) -> str:
        sm = SettingsManager(api)
        notification_settings = await api.conversation_manager.repo.get_notification_settings() or {"notifications_enabled": True, "notifications_silent": False}
        return sm.get_notifications_submenu_text(notification_settings)

    async def build_notifications_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
        sm = SettingsManager(api)
        notification_settings = await api.conversation_manager.repo.get_notification_settings() or {"notifications_enabled": True, "notifications_silent": False}
        return sm.get_notifications_submenu_keyboard(notification_settings)

    async def handle_notifications_button(data: str, flow_state, api, user_id) -> Optional[str]:
        if data == "toggle_notifications":
            ns = await api.conversation_manager.repo.get_notification_settings()
            new_value = not bool(ns.get("notifications_enabled", True))
            success = await api.conversation_manager.repo.update_notification_settings(notifications_enabled=new_value)
            if not success:
                await api.message_manager.send_text(user_id, "❌ Failed to update settings", vanish=True, conv=False, delete_after=3)
            return STATE_NOTIFICATIONS

        if data == "toggle_silent":
            ns = await api.conversation_manager.repo.get_notification_settings()
            if not ns.get("notifications_enabled", True):
                # notify via popup-like message
                await api.message_manager.send_text(user_id, "❌ Enable notifications first!", vanish=True, conv=False, delete_after=3)
                return STATE_NOTIFICATIONS
            new_value = not bool(ns.get("notifications_silent", False))
            success = await api.conversation_manager.repo.update_notification_settings(notifications_silent=new_value)
            if not success:
                await api.message_manager.send_text(user_id, "❌ Failed to update settings", vanish=True, conv=False, delete_after=3)
            return STATE_NOTIFICATIONS

        return None

    flow.add_state(make_state(
        STATE_NOTIFICATIONS,
        text_builder=build_notifications_text,
        keyboard_builder=build_notifications_keyboard,
        action=MessageAction.EDIT,
        timeout=120,
        on_button_press=handle_notifications_button,
        back_button=CommonCallbacks.BACK,
        parent_state=STATE_ADMIN,
    ))

    # ------------------ Debts ------------------
    async def build_debts_text(flow_state, api, user_id) -> str:
        ds = await api.conversation_manager.repo.get_debt_settings()
        sm = SettingsManager(api)
        return sm.get_debts_submenu_text(ds)

    async def build_debts_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
        sm = SettingsManager(api)
        return sm.get_debts_submenu_keyboard()

    async def handle_debts_button(data: str, flow_state, api, user_id) -> Optional[str]:
        if data == "debt_method":
            return STATE_DEBT_METHOD
        if data == "debt_threshold":
            return STATE_DEBT_THRESHOLD
        return None

    flow.add_state(make_state(
        STATE_DEBTS,
        text_builder=build_debts_text,
        keyboard_builder=build_debts_keyboard,
        action=MessageAction.EDIT,
        timeout=120,
        on_button_press=handle_debts_button,
        back_button=CommonCallbacks.BACK,
        parent_state=STATE_ADMIN,
    ))

    # Debt method selector
    async def build_debt_method_text(flow_state, api, user_id) -> str:
        ds = await api.conversation_manager.repo.get_debt_settings()
        return (
            "🧮 **Debt Correction Method**\n\n"
            f"**Current method:** {('Absolute' if (ds.correction_method == 'absolute') else 'Proportional')}\n\n"
            "Choose a method:"
        )

    async def handle_debt_method_button(data: str, flow_state, api, user_id) -> Optional[str]:
        if data not in ("debt_method_absolute", "debt_method_proportional"):
            return None
        new_method = "absolute" if data == "debt_method_absolute" else "proportional"
        success = await api.conversation_manager.repo.update_debt_settings(correction_method=new_method)
        if not success:
            await api.message_manager.send_text(user_id, "❌ Failed to save settings. Please try again.", vanish=True, conv=False, delete_after=3)
            return STATE_DEBT_METHOD
        return STATE_DEBTS

    flow.add_state(make_state(
        STATE_DEBT_METHOD,
        text_builder=build_debt_method_text,
        buttons=[
            [ButtonCallback("🧮 Absolute", "debt_method_absolute"), ButtonCallback("📊 Proportional", "debt_method_proportional")],
            [ButtonCallback("◁ Back", CommonCallbacks.BACK)],
        ],
        action=MessageAction.EDIT,
        timeout=120,
        on_button_press=handle_debt_method_button,
        back_button=CommonCallbacks.BACK,
        parent_state=STATE_DEBTS,
    ))

    # Debt threshold input
    async def build_debt_threshold_text(flow_state, api, user_id) -> str:
        ds = await api.conversation_manager.repo.get_debt_settings()
        return (
            "🔢 **Debt Correction Threshold**\n\n"
            f"Current value: {int(ds.correction_threshold)}\n\n"
            "Enter a new threshold (0-50):"
        )

    async def handle_debt_threshold_input(input_text: str, flow_state, api, user_id) -> Optional[str]:
        parser = IntegerParser()
        val = parser.parse(input_text)
        if val is None or val < 0 or val > 50:
            await api.message_manager.send_text(user_id, "❌ Invalid input. Please enter a number between 0 and 50.", vanish=True, conv=True, delete_after=3)
            return STATE_DEBT_THRESHOLD
        success = await api.conversation_manager.repo.update_debt_settings(correction_threshold=val)
        if not success:
            await api.message_manager.send_text(user_id, "❌ Failed to save settings. Please try again.", vanish=True, conv=False, delete_after=3)
            return STATE_DEBT_THRESHOLD
        return STATE_DEBTS

    flow.add_state(MessageDefinition(
        state_id=STATE_DEBT_THRESHOLD,
        state_type=StateType.TEXT_INPUT,
        text_builder=build_debt_threshold_text,
        input_storage_key=STATE_DEBT_THRESHOLD,
        on_input_received=handle_debt_threshold_input,
        action=MessageAction.EDIT,
        back_button=CommonCallbacks.BACK,
        parent_state=STATE_DEBTS,
    ))

    # ------------------ Google Sheets ------------------
    async def build_gsheet_text(flow_state, api, user_id) -> str:
        g = await api.conversation_manager.repo.get_gsheet_settings()
        sm = SettingsManager(api)
        return sm.get_gsheet_submenu_text(g)

    async def build_gsheet_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
        g = await api.conversation_manager.repo.get_gsheet_settings()
        sm = SettingsManager(api)
        return sm.get_gsheet_submenu_keyboard(g)

    async def handle_gsheet_button(data: str, flow_state, api, user_id) -> Optional[str]:
        g = await api.conversation_manager.repo.get_gsheet_settings()
        if data == "set_period":
            return STATE_GSHEET_SET_PERIOD
        if data == "toggle_periodic":
            new_value = not bool(getattr(g, "periodic_sync_enabled", False))
            success = await api.conversation_manager.repo.update_gsheet_settings(periodic_sync_enabled=new_value)
            if not success:
                await api.message_manager.send_text(user_id, "❌ Failed to save settings. Please try again.", vanish=True, conv=False, delete_after=3)
            return STATE_GSHEET
        if data == "toggle_two_way":
            new_value = not bool(getattr(g, "two_way_sync_enabled", False))
            success = await api.conversation_manager.repo.update_gsheet_settings(two_way_sync_enabled=new_value)
            if not success:
                await api.message_manager.send_text(user_id, "❌ Failed to save settings. Please try again.", vanish=True, conv=False, delete_after=3)
            return STATE_GSHEET
        if data == "toggle_after_actions":
            new_value = not bool(getattr(g, "sync_after_actions_enabled", False))
            success = await api.conversation_manager.repo.update_gsheet_settings(sync_after_actions_enabled=new_value)
            if not success:
                await api.message_manager.send_text(user_id, "❌ Failed to save settings. Please try again.", vanish=True, conv=False, delete_after=3)
            return STATE_GSHEET
        return None

    flow.add_state(make_state(
        STATE_GSHEET,
        text_builder=build_gsheet_text,
        keyboard_builder=build_gsheet_keyboard,
        action=MessageAction.EDIT,
        timeout=120,
        on_button_press=handle_gsheet_button,
        back_button=CommonCallbacks.BACK,
        parent_state=STATE_ADMIN,
    ))

    # GSheet set period input
    async def build_gsheet_period_text(flow_state, api, user_id) -> str:
        g = await api.conversation_manager.repo.get_gsheet_settings()
        return (
            "⏱ **Google Sheets Sync Period**\n\n"
            f"Current value: {int(getattr(g, 'sync_period_minutes', 60))} min\n\n"
            "Enter a new period in minutes (1-1440):"
        )

    async def handle_gsheet_period_input(input_text: str, flow_state, api, user_id) -> Optional[str]:
        parser = IntegerParser()
        val = parser.parse(input_text)
        if val is None or val < 1 or val > 24 * 60:
            await api.message_manager.send_text(user_id, "❌ Invalid input. Please enter a number between 1 and 1440.", vanish=True, conv=True, delete_after=3)
            return STATE_GSHEET_SET_PERIOD
        success = await api.conversation_manager.repo.update_gsheet_settings(sync_period_minutes=val)
        if not success:
            await api.message_manager.send_text(user_id, "❌ Failed to save settings. Please try again.", vanish=True, conv=False, delete_after=3)
            return STATE_GSHEET_SET_PERIOD
        return STATE_GSHEET

    flow.add_state(MessageDefinition(
        state_id=STATE_GSHEET_SET_PERIOD,
        state_type=StateType.TEXT_INPUT,
        text_builder=build_gsheet_period_text,
        input_storage_key=STATE_GSHEET_SET_PERIOD,
        on_input_received=handle_gsheet_period_input,
        action=MessageAction.EDIT,
        back_button=CommonCallbacks.BACK,
        parent_state=STATE_GSHEET,
    ))

    # ------------------ Snapshots ------------------
    async def build_snapshots_text(flow_state, api, user_id) -> str:
        s = await api.conversation_manager.repo.get_snapshot_settings()
        sm = SettingsManager(api)
        return sm.get_snapshots_submenu_text(s)

    async def build_snapshots_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
        s = await api.conversation_manager.repo.get_snapshot_settings()
        sm = SettingsManager(api)
        return sm.get_snapshots_submenu_keyboard(s)

    async def handle_snapshots_button(data: str, flow_state, api, user_id) -> Optional[str]:
        if data == "set_keep_last":
            return STATE_SNAPSHOT_SET_KEEP_LAST
        if data == "creation_points":
            return STATE_SNAPSHOT_CREATION_POINTS
        return None

    flow.add_state(make_state(
        STATE_SNAPSHOTS,
        text_builder=build_snapshots_text,
        keyboard_builder=build_snapshots_keyboard,
        action=MessageAction.EDIT,
        timeout=120,
        on_button_press=handle_snapshots_button,
        back_button=CommonCallbacks.BACK,
        parent_state=STATE_ADMIN,
    ))

    # Set keep last input
    async def build_snapshots_keep_text(flow_state, api, user_id) -> str:
        s = await api.conversation_manager.repo.get_snapshot_settings()
        return (
            "🔢 **Snapshots: Keep Last**\n\n"
            f"Current value: {int(getattr(s, 'keep_last', 10))}\n\n"
            "Enter how many snapshots to keep (1-200):"
        )

    async def handle_snapshots_keep_input(input_text: str, flow_state, api, user_id) -> Optional[str]:
        parser = IntegerParser()
        val = parser.parse(input_text)
        if val is None or val < 1 or val > 200:
            await api.message_manager.send_text(user_id, "❌ Invalid input. Please enter a number between 1 and 200.", vanish=True, conv=True, delete_after=3)
            return STATE_SNAPSHOT_SET_KEEP_LAST
        success = await api.conversation_manager.repo.update_snapshot_settings(keep_last=val)
        if not success:
            await api.message_manager.send_text(user_id, "❌ Failed to save settings. Please try again.", vanish=True, conv=False, delete_after=3)
            return STATE_SNAPSHOT_SET_KEEP_LAST
        return STATE_SNAPSHOTS

    flow.add_state(MessageDefinition(
        state_id=STATE_SNAPSHOT_SET_KEEP_LAST,
        state_type=StateType.TEXT_INPUT,
        text_builder=build_snapshots_keep_text,
        input_storage_key=STATE_SNAPSHOT_SET_KEEP_LAST,
        on_input_received=handle_snapshots_keep_input,
        action=MessageAction.EDIT,
        back_button=CommonCallbacks.BACK,
        parent_state=STATE_SNAPSHOTS,
    ))

    # Snapshot creation points toggles
    async def build_snapshot_creation_text(flow_state, api, user_id) -> str:
        s = await api.conversation_manager.repo.get_snapshot_settings()
        sm = SettingsManager(api)
        return sm.get_snapshots_creation_points_submenu_text(s)

    async def build_snapshot_creation_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
        s = await api.conversation_manager.repo.get_snapshot_settings()
        sm = SettingsManager(api)
        return sm.get_snapshots_creation_points_submenu_keyboard(s)

    async def handle_snapshot_creation_button(data: str, flow_state, api, user_id) -> Optional[str]:
        s = await api.conversation_manager.repo.get_snapshot_settings()
        if data == "toggle_card_closed":
            new = not bool(getattr(s, "card_closed", True))
            success = await api.conversation_manager.repo.update_snapshot_settings(card_closed=new)
            if not success:
                await api.message_manager.send_text(user_id, "❌ Failed to save settings. Please try again.", vanish=True, conv=False, delete_after=3)
            return STATE_SNAPSHOT_CREATION_POINTS
        if data == "toggle_session_completed":
            new = not bool(getattr(s, "session_completed", True))
            success = await api.conversation_manager.repo.update_snapshot_settings(session_completed=new)
            if not success:
                await api.message_manager.send_text(user_id, "❌ Failed to save settings. Please try again.", vanish=True, conv=False, delete_after=3)
            return STATE_SNAPSHOT_CREATION_POINTS
        if data == "toggle_quick_order":
            new = not bool(getattr(s, "quick_order", False))
            success = await api.conversation_manager.repo.update_snapshot_settings(quick_order=new)
            if not success:
                await api.message_manager.send_text(user_id, "❌ Failed to save settings. Please try again.", vanish=True, conv=False, delete_after=3)
            return STATE_SNAPSHOT_CREATION_POINTS
        if data == "toggle_card_created":
            new = not bool(getattr(s, "card_created", True))
            success = await api.conversation_manager.repo.update_snapshot_settings(card_created=new)
            if not success:
                await api.message_manager.send_text(user_id, "❌ Failed to save settings. Please try again.", vanish=True, conv=False, delete_after=3)
            return STATE_SNAPSHOT_CREATION_POINTS
        return None

    flow.add_state(make_state(
        STATE_SNAPSHOT_CREATION_POINTS,
        text_builder=build_snapshot_creation_text,
        keyboard_builder=build_snapshot_creation_keyboard,
        action=MessageAction.EDIT,
        timeout=120,
        on_button_press=handle_snapshot_creation_button,
        back_button=CommonCallbacks.BACK,
        parent_state=STATE_SNAPSHOTS,
    ))

    # Short success exit state used when user presses 'Done' in main settings
    flow.add_state(ExitStateBuilder.create(state_id=STATE_SAVE_RESULT, text="✅ Settings saved!", timeout=1, delete_after=0))

    return flow
