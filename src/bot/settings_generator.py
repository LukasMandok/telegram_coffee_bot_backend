from typing import Any, Callable, Dict, List, Optional
import logging

from .settings_schema import CATEGORIES, SettingType
from .message_flow_helpers import (
    make_state,
    NavigationButtons,
    toggle_button,
    IntegerParser,
)
from .message_flow import (
    MessageDefinition,
    MessageAction,
    StateType,
    ButtonCallback,
    NumericValidator,
)
from .message_flow_helpers import CommonCallbacks

logger = logging.getLogger(__name__)

# Icon map for buttons
ICON_MAP: Dict[str, str] = {
    "group_page_size": "📄",
    "group_sort_by": "🔤",
    "vanishing_enabled": "💬",
    "vanishing_threshold": "🔢",
    "notifications_enabled": "🔔",
    "notifications_silent": "🔕",
    "log_level": "📊",
    "log_show_time": "⏱",
    "log_show_caller": "👤",
    "log_show_class": "🏷",
    "periodic_sync_enabled": "⏱",
    "sync_period_minutes": "⏱",
    "two_way_sync_enabled": "🔁",
    "sync_after_actions_enabled": "🔄",
    "keep_last": "🧾",
    "card_closed": "📌",
    "session_completed": "✅",
    "quick_order": "⚡",
    "card_created": "➕",
    "correction_method": "🧮",
    "correction_threshold": "🔢",
    "creditor_exempt_from_correction": "👑",
    "creditor_free_coffees": "☕",
}


# Map app section -> (getter_name, updater_name) on repository
SECTION_REPO_MAP: Dict[str, tuple[str, str]] = {
    "logging": ("get_log_settings", "update_log_settings"),
    "notifications": ("get_notification_settings", "update_notification_settings"),
    "debt": ("get_debt_settings", "update_debt_settings"),
    "gsheet": ("get_gsheet_settings", "update_gsheet_settings"),
    "snapshots": ("get_snapshot_settings", "update_snapshot_settings"),
}


def _find_setting(category: str, key: str, scope: Optional[str] = None):
    """Find a setting by key within a category, preferring scope-aware match.

    If scope == 'main' prefer user-target settings, if scope == 'admin' prefer app-target.
    Falls back to the first matching key if no scoped match is found.
    """
    settings = CATEGORIES.get(category, {}).get("settings", [])
    if scope == "main":
        for s in settings:
            if s.key == key and s.target == "user":
                return s
    if scope == "admin":
        for s in settings:
            if s.key == key and s.target == "app":
                return s
    for s in settings:
        if s.key == key:
            return s
    return None


def register_schema_states(
    flow,
    user_update_cb: Callable[[Any, Any, int], Any],
    global_update_cb: Callable[[Any, Any, int, Any], Any],
    *,
    parent_main: Optional[str] = None,
    parent_admin: Optional[str] = None,
):
    """
    Register schema-driven states onto the given MessageFlow instance.

    - `user_update_cb(flow_state, api, user_id, **kwargs)` updates user settings
    - `global_update_cb(flow_state, api, user_id, coro)` awaits app-level update coroutine
    """

    for category_key, meta in CATEGORIES.items():
        main_state_id = f"sd:cat:main:{category_key}"
        admin_state_id = f"sd:cat:admin:{category_key}"

        async def _text_builder(flow_state, api, user_id, _meta=meta):
            # Build a compact text showing current values
            repo = api.conversation_manager.repo
            parts: List[str] = [f"**{_meta.get('label')}**", ""]
            for s in _meta.get("settings", []):
                try:
                    if s.target == "user":
                        us = await repo.get_user_settings(user_id)
                        cur = getattr(us, s.key, s.default) if us is not None else s.default
                    else:
                        getter_name = SECTION_REPO_MAP.get(s.section, (None, None))[0]
                        getter = getattr(repo, getter_name) if getter_name else None
                        val = await getter() if getter is not None else None
                        if isinstance(val, dict):
                            cur = val.get(s.key, s.default)
                        else:
                            cur = getattr(val, s.key, s.default) if val is not None else s.default
                except Exception:
                    cur = s.default

                icon = ICON_MAP.get(s.key, "")
                label_base = f"{icon} {s.label}" if icon else s.label
                parts.append(f"• {label_base}: {cur}")
                if getattr(s, "description", None):
                    parts.append(f"   {s.description}")
            parts.append("")
            parts.append("Select a setting to adjust:")
            return "\n".join(parts)


        def _make_keyboard_builder_for(scope: str):
            async def _keyboard_builder(flow_state, api, user_id, _cat=category_key):
                repo = api.conversation_manager.repo
                buttons: List[List[ButtonCallback]] = []
                for s in CATEGORIES[_cat]["settings"]:
                    # filter by scope: main -> user settings only, admin -> app settings only
                    if scope == "main" and s.target != "user":
                        continue
                    if scope == "admin" and s.target != "app":
                        continue
                    try:
                        if s.target == "user":
                            us = await repo.get_user_settings(user_id)
                            cur = getattr(us, s.key, s.default) if us is not None else s.default
                        else:
                            getter_name = SECTION_REPO_MAP.get(s.section, (None, None))[0]
                            getter = getattr(repo, getter_name) if getter_name else None
                            val = await getter() if getter is not None else None
                            if isinstance(val, dict):
                                cur = val.get(s.key, s.default)
                            else:
                                cur = getattr(val, s.key, s.default) if val is not None else s.default
                    except Exception:
                        cur = s.default

                    icon = ICON_MAP.get(s.key, "")
                    label_base = f"{icon} {s.label}" if icon else s.label

                    if s.type == SettingType.TOGGLE:
                        callback_data = f"sd:toggle:{_cat}:{s.key}"
                        tb = toggle_button(bool(cur), label_base, callback_data)
                        buttons.append([tb])
                    elif s.type == SettingType.ENUM:
                        callback_data = f"sd:enum:{_cat}:{s.key}"
                        buttons.append([ButtonCallback(f"{label_base}: {cur}", callback_data)])
                    elif s.type == SettingType.NUMBER:
                        callback_data = f"sd:number_edit:{_cat}:{s.key}"
                        buttons.append([ButtonCallback(f"{label_base}: {cur}", callback_data)])

                buttons.append([ButtonCallback("◁ Back", CommonCallbacks.BACK)])
                return buttons

            return _keyboard_builder


        # Create two on_button handlers so we can return the correct
        # next-state id (main vs admin) depending on where the user came from.
        def _make_on_button(scope: str):
            async def _on_button_press(data: str, flow_state, api, user_id) -> Optional[str]:
                repo = api.conversation_manager.repo
                if not data:
                    return None

                # sd:toggle:<category>:<key>
                if data.startswith("sd:toggle:"):
                    parts = data.split(":", 3)
                    if len(parts) < 4:
                        logger.warning("settings callback malformed: expected 4 parts, got %d", len(parts))
                        return None
                    _, _typ, cat, key = parts
                    s = _find_setting(cat, key, scope)
                    if not s:
                        logger.warning("setting not found for category=%s key=%s scope=%s", cat, key, scope)
                        return None

                    # fetch current
                    if s.target == "user":
                        us = await repo.get_user_settings(user_id)
                        cur = bool(getattr(us, key, False)) if us is not None else bool(s.default)
                        new_val = not cur
                        ok = await user_update_cb(flow_state, api, user_id, **{key: new_val})
                        return f"sd:cat:{scope}:{cat}" if ok else f"sd:cat:{scope}:{cat}"
                    else:
                        getter_name, updater_name = SECTION_REPO_MAP.get(s.section, (None, None))
                        if not updater_name:
                            return f"sd:cat:{scope}:{cat}"
                        # read current
                        getter = getattr(repo, getter_name) if getter_name else None
                        val = await getter() if getter is not None else None
                        if isinstance(val, dict):
                            cur = bool(val.get(key, s.default))
                        else:
                            cur = bool(getattr(val, key, s.default)) if val is not None else bool(s.default)
                        new_val = not cur
                        updater = getattr(repo, updater_name)
                        coro = updater(**{key: new_val})
                        ok = await global_update_cb(flow_state, api, user_id, coro)
                        return f"sd:cat:{scope}:{cat}" if ok else f"sd:cat:{scope}:{cat}"

                # sd:enum:<category>:<key>  -> cycle
                if data.startswith("sd:enum:"):
                    parts = data.split(":", 3)
                    if len(parts) < 4:
                        return None
                    _, _typ, cat, key = parts
                    s = _find_setting(cat, key, scope)
                    if not s or not s.options:
                        return None

                    # fetch current
                    if s.target == "user":
                        us = await repo.get_user_settings(user_id)
                        cur = getattr(us, key, s.default) if us is not None else s.default
                        opts = s.options
                        try:
                            idx = opts.index(cur)
                        except ValueError:
                            idx = -1
                        new_val = opts[(idx + 1) % len(opts)]
                        ok = await user_update_cb(flow_state, api, user_id, **{key: new_val})
                        return f"sd:cat:{scope}:{cat}" if ok else f"sd:cat:{scope}:{cat}"
                    else:
                        getter_name, updater_name = SECTION_REPO_MAP.get(s.section, (None, None))
                        getter = getattr(repo, getter_name) if getter_name else None
                        val = await getter() if getter is not None else None
                        if isinstance(val, dict):
                            cur = val.get(key, s.default)
                        else:
                            cur = getattr(val, key, s.default) if val is not None else s.default
                        opts = s.options
                        try:
                            idx = opts.index(cur)
                        except ValueError:
                            idx = -1
                        new_val = opts[(idx + 1) % len(opts)]
                        updater = getattr(repo, updater_name)
                        coro = updater(**{key: new_val})
                        ok = await global_update_cb(flow_state, api, user_id, coro)
                        return f"sd:cat:{scope}:{cat}" if ok else f"sd:cat:{scope}:{cat}"

                # sd:number_edit:<category>:<key> -> open input state
                if data.startswith("sd:number_edit:"):
                    parts = data.split(":", 3)
                    if len(parts) < 4:
                        return None
                    _, _typ, cat, key = parts
                    # navigate to input state
                    return f"sd:input:{scope}:{cat}:{key}"

                return None

            return _on_button_press

        # Register category states for both main and admin contexts so back/navigation
        # returns to the correct parent depending on where the user opened the category.
        flow.add_state(make_state(
            main_state_id,
            text_builder=_text_builder,
            keyboard_builder=_make_keyboard_builder_for('main'),
            action=MessageAction.EDIT,
            timeout=120,
            on_button_press=_make_on_button('main'),
            back_button=CommonCallbacks.BACK,
            parent_state=parent_main,
        ))

        flow.add_state(make_state(
            admin_state_id,
            text_builder=_text_builder,
            keyboard_builder=_make_keyboard_builder_for('admin'),
            action=MessageAction.EDIT,
            timeout=120,
            on_button_press=_make_on_button('admin'),
            back_button=CommonCallbacks.BACK,
            parent_state=parent_admin,
        ))

        # Register number-input sub-states for number settings in this category
        for s in meta.get("settings", []):
            if s.type != SettingType.NUMBER:
                continue

            input_state_id_main = f"sd:input:main:{category_key}:{s.key}"
            input_state_id_admin = f"sd:input:admin:{category_key}:{s.key}"

            async def _input_text_builder(flow_state, api, user_id, _s=s, _cat=category_key):
                repo = api.conversation_manager.repo
                if _s.target == "user":
                    us = await repo.get_user_settings(user_id)
                    cur = getattr(us, _s.key, _s.default) if us is not None else _s.default
                else:
                    getter_name = SECTION_REPO_MAP.get(_s.section, (None, None))[0]
                    getter = getattr(repo, getter_name) if getter_name else None
                    val = await getter() if getter is not None else None
                    if isinstance(val, dict):
                        cur = val.get(_s.key, _s.default)
                    else:
                        cur = getattr(val, _s.key, _s.default) if val is not None else _s.default

                return (
                    f"🔢 **{_s.label}**\n\n"
                    f"Current value: {cur}\n\n"
                    f"Enter a new value ({_s.min_value or ''}-{_s.max_value or ''}):"
                )

            async def _handle_input_received_main(input_text: str, flow_state, api, user_id, _s=s, _cat=category_key):
                parser = IntegerParser()
                val = parser.parse(input_text)
                if val is None:
                    await api.message_manager.send_text(user_id, f"❌ Invalid input. Please enter a whole number.", vanish=True, conv=True, delete_after=3)
                    return input_state_id_main
                if _s.min_value is not None and val < _s.min_value:
                    await api.message_manager.send_text(user_id, f"❌ Value must be at least {_s.min_value}.", vanish=True, conv=True, delete_after=3)
                    return input_state_id_main
                if _s.max_value is not None and val > _s.max_value:
                    await api.message_manager.send_text(user_id, f"❌ Value must be at most {_s.max_value}.", vanish=True, conv=True, delete_after=3)

                repo = api.conversation_manager.repo
                if _s.target == "user":
                    ok = await user_update_cb(flow_state, api, user_id, **{_s.key: val})
                    return f"sd:cat:main:{_cat}" if ok else input_state_id_main
                else:
                    getter_name, updater_name = SECTION_REPO_MAP.get(_s.section, (None, None))
                    updater = getattr(repo, updater_name)
                    coro = updater(**{_s.key: val})
                    ok = await global_update_cb(flow_state, api, user_id, coro)
                    if ok:
                        await api.message_manager.send_text(
                            user_id,
                            f"✅ {_s.label} updated",
                            vanish=True,
                            conv=False,
                            delete_after=2,
                        )
                    return f"sd:cat:main:{_cat}" if ok else input_state_id_main

            validator = NumericValidator(min_value=s.min_value, max_value=s.max_value)
            # main input state
            flow.add_state(MessageDefinition(
                state_id=input_state_id_main,
                state_type=StateType.TEXT_INPUT,
                text_builder=_input_text_builder,
                input_storage_key=input_state_id_main,
                input_validator=validator,
                on_input_received=_handle_input_received_main,
                action=MessageAction.EDIT,
                back_button=CommonCallbacks.BACK,
                parent_state=main_state_id,
            ))

            # admin input state — reuse builder but return to admin cat on success
            async def _handle_input_received_admin(input_text: str, flow_state, api, user_id, _s=s, _cat=category_key):
                parser = IntegerParser()
                val = parser.parse(input_text)
                if val is None:
                    await api.message_manager.send_text(user_id, f"❌ Invalid input. Please enter a whole number.", vanish=True, conv=True, delete_after=3)
                    return input_state_id_admin
                if _s.min_value is not None and val < _s.min_value:
                    await api.message_manager.send_text(user_id, f"❌ Value must be at least {_s.min_value}.", vanish=True, conv=True, delete_after=3)
                    return input_state_id_admin
                if _s.max_value is not None and val > _s.max_value:
                    await api.message_manager.send_text(user_id, f"❌ Value must be at most {_s.max_value}.", vanish=True, conv=True, delete_after=3)

                repo = api.conversation_manager.repo
                if _s.target == "user":
                    ok = await user_update_cb(flow_state, api, user_id, **{_s.key: val})
                    return f"sd:cat:admin:{_cat}" if ok else input_state_id_admin
                else:
                    getter_name, updater_name = SECTION_REPO_MAP.get(_s.section, (None, None))
                    updater = getattr(repo, updater_name)
                    coro = updater(**{_s.key: val})
                    ok = await global_update_cb(flow_state, api, user_id, coro)
                    if ok:
                        await api.message_manager.send_text(
                            user_id,
                            f"✅ {_s.label} updated",
                            vanish=True,
                            conv=False,
                            delete_after=2,
                        )
                    return f"sd:cat:admin:{_cat}" if ok else input_state_id_admin

            flow.add_state(MessageDefinition(
                state_id=input_state_id_admin,
                state_type=StateType.TEXT_INPUT,
                text_builder=_input_text_builder,
                input_storage_key=input_state_id_admin,
                input_validator=validator,
                on_input_received=_handle_input_received_admin,
                action=MessageAction.EDIT,
                back_button=CommonCallbacks.BACK,
                parent_state=admin_state_id,
            ))


def build_admin_submenu_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
    """Helper keyboard to show admin categories (used by Settings flow).

    Only include categories that expose app-level settings. Prefer manual
    admin routes for complex categories (logging, notifications, debts,
    gsheet, snapshots) so their specialized submenus are used.
    """
    buttons: List[List[ButtonCallback]] = []
    manual_admin_routes: Dict[str, str] = {
        "logging": "logging",
        "notifications": "notifications",
        "debts": "debts",
        "gsheet": "gsheet",
        "snapshots": "snapshots",
    }

    # icons for categories
    cat_icons: Dict[str, str] = {
        "ordering": "📋",
        "vanishing": "💬",
        "notifications": "🔔",
        "logging": "📊",
        "gsheet": "📄",
        "debts": "💳",
        "snapshots": "📸",
    }

    for key, meta in CATEGORIES.items():
        # include only categories that have at least one app-target setting
        settings = meta.get("settings", [])
        has_app = any(getattr(s, "target", None) == "app" for s in settings)
        if not has_app:
            continue

        label = meta.get("label") or key
        icon = cat_icons.get(key, "")

        if key in manual_admin_routes:
            cb = manual_admin_routes[key]
        else:
            cb = f"sd:cat:admin:{key}"

        text = f"{icon} {label}" if icon else label
        buttons.append([ButtonCallback(text, cb)])

    buttons.append([ButtonCallback("🔐 Registration Password", "registration_password")])
    buttons.append([ButtonCallback("◁ Back", CommonCallbacks.BACK)])
    return buttons


__all__ = ["register_schema_states", "build_admin_submenu_keyboard"]
