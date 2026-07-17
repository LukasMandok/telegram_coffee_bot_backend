"""Helpers for the logging-modules special-case MessageFlow state."""

from typing import Callable, List, Optional, Tuple

from ...common.log import LOG_LEVEL_STATE_ICON, LOG_STATE_SEQUENCE, format_log_state, get_known_loggers
from .custom_route_contracts import register_custom_route
from ..message_flow import ButtonCallback
from ..message_flow_helpers import CommonCallbacks, StagingManager, apply_update_or_notify
from ..settings_ui import SettingsUi


@register_custom_route("logging_modules")
def build_logging_modules_state_helpers(
    *,
    state_logging_modules: str,
    state_logging: str,
) -> Tuple[Callable, Callable, Callable, Callable, Callable]:
    """Build state helper callables for the logging-modules special flow."""

    async def _ensure_logging_current_state(flow_state, api):
        db_log_settings = await api.conversation_manager.repo.get_log_settings() or {}
        global_level = (db_log_settings.get("log_level") or "INFO").upper()
        if global_level not in LOG_STATE_SEQUENCE:
            global_level = "INFO"
        if flow_state.get("logging_current_state_index") is None:
            idx = LOG_STATE_SEQUENCE.index(global_level) if global_level in LOG_STATE_SEQUENCE else 2
            flow_state.set("logging_current_state_index", idx)

    async def pagination_items_builder(flow_state, api, user_id):
        repo = api.conversation_manager.repo
        db_log_settings = await repo.get_log_settings() or {}
        global_level = (db_log_settings.get("log_level") or "INFO").upper()
        if global_level not in LOG_STATE_SEQUENCE:
            global_level = "INFO"

        staging = StagingManager(flow_state, "logging_pending_overrides")
        pending = staging.get_staged() or {}
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

            icon = LOG_LEVEL_STATE_ICON.get(state, "")
            label = f"{display} {icon}{suffix}".strip()
            items.append((module_name, label))

        return items

    def pagination_item_button_builder(item, idx):
        module_name, label = item

        def _make_toggle(module: str):
            async def _handler(flow_state, api, user_id) -> Optional[str]:
                await _ensure_logging_current_state(flow_state, api)
                staging = StagingManager(flow_state, "logging_pending_overrides")
                cur_idx = int(flow_state.get("logging_current_state_index") or 0)
                target_level = LOG_STATE_SEQUENCE[cur_idx]

                if module in staging.get_staged():
                    staging.unstage(module)
                else:
                    staging.stage(module, target_level)

                # Refresh pagination items so labels update immediately.
                try:
                    if state_logging_modules in flow_state.pagination_state:
                        new_items = await pagination_items_builder(flow_state, api, user_id)
                        flow_state.pagination_state[state_logging_modules]["items"] = new_items
                except Exception:
                    pass
                return state_logging_modules

            return _handler

        return ButtonCallback(label, f"m_{idx}", callback_handler=_make_toggle(module_name))

    async def pagination_extras(flow_state, api, user_id) -> List[List[ButtonCallback]]:
        db_log_settings = await api.conversation_manager.repo.get_log_settings() or {}
        global_level = (db_log_settings.get("log_level") or "INFO").upper()
        current_state_index = flow_state.get("logging_current_state_index")
        if current_state_index is None:
            current_state_index = LOG_STATE_SEQUENCE.index(global_level) if global_level in LOG_STATE_SEQUENCE else 2
            flow_state.set("logging_current_state_index", current_state_index)

        current_state = LOG_STATE_SEQUENCE[int(flow_state.get("logging_current_state_index"))]
        current_icon = LOG_LEVEL_STATE_ICON.get(current_state, "")

        header = [
            ButtonCallback("⬅", "state_prev"),
            ButtonCallback(f"{current_state} {current_icon}", "noop"),
            ButtonCallback("➡", "state_next"),
        ]

        footer = [
            ButtonCallback(f"{SettingsUi.ICON_BACK} Back", CommonCallbacks.BACK),
            ButtonCallback("✅ Apply", "apply"),
        ]

        return [header, footer]

    async def build_logging_modules_text(flow_state, api, user_id) -> str:
        db_log_settings = await api.conversation_manager.repo.get_log_settings() or {}
        global_level = (db_log_settings.get("log_level") or "INFO").upper()
        current_index = flow_state.get("logging_current_state_index")
        if current_index is None:
            current_index = LOG_STATE_SEQUENCE.index(global_level) if global_level in LOG_STATE_SEQUENCE else 2
            flow_state.set("logging_current_state_index", current_index)

        staging = StagingManager(flow_state, "logging_pending_overrides")
        pending = staging.get_staged() or {}
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
            return state_logging_modules
        if data == "state_next":
            idx = int(flow_state.get("logging_current_state_index") or 0)
            idx = (idx - 1) % len(LOG_STATE_SEQUENCE)
            flow_state.set("logging_current_state_index", idx)
            return state_logging_modules
        if data == "apply":
            staging = StagingManager(flow_state, "logging_pending_overrides")
            pending = staging.get_staged() or {}
            db_log_settings = await api.conversation_manager.repo.get_log_settings() or {}
            existing_overrides = dict(db_log_settings.get("log_module_overrides", {}) or {})
            new_overrides = dict(existing_overrides)
            for key, value in (pending or {}).items():
                new_overrides[key] = value
            ok = await apply_update_or_notify(
                flow_state,
                api,
                user_id,
                api.conversation_manager.repo.update_log_settings(log_module_overrides=new_overrides),
                error_text="❌ Failed to apply module logging settings.",
            )
            if not ok:
                return state_logging_modules
            staging.clear()
            return state_logging
        if data == "noop":
            return state_logging_modules

        # Module toggle callbacks are handled via per-button callback_handler.
        return None

    return (
        pagination_items_builder,
        pagination_item_button_builder,
        pagination_extras,
        build_logging_modules_text,
        handle_logging_modules_button,
    )
