"""
Settings Flow Generator

Dynamically generates MessageFlow states from the settings schema.

This module handles:
1. Generic state generation for standard setting types (toggle, number, enum)
2. State registration onto MessageFlow instances
3. Routing of special cases to custom handlers

Philosophy:
- Schema-driven: derives all state logic from settings_schema.CATEGORIES
- Type-generic: handles toggle/number/enum generically
- Custom-isolated: routes special cases (logging format, module overrides) to dedicated states
- Handler-based: updates happen via repository methods, passed as callbacks
"""

from typing import Any, Callable, Dict, List, Optional
import logging

from .message_flow import (
    ButtonCallback,
    MessageAction,
    MessageDefinition,
    MessageFlow,
    StateType,
)
from .message_flow_helpers import (
    CommonCallbacks,
    IntegerParser,
    make_state,
    compact_toggle_button,
    toggle_button,
)
from .settings_schema import CATEGORIES, ICON_MAP, SECTION_REPO_MAP, SettingCategory, SettingType, Setting


logger = logging.getLogger(__name__)

from ..common.log import LOG_LEVEL_STATE_ICON

# ===========================

# Helper Functions
# ===========================


async def _get_current_setting_value(
    repo, 
    setting: Setting,
    user_id: Optional[int] = None
) -> Any:
    """
    Fetch current value of a setting from repository.
    
    Args:
        repo: Repository instance (has getter methods)
        setting: Setting definition with target/section info
        user_id: User ID (required if target=="user")
    
    Returns:
        Current setting value, or default if not found
    """
    try:
        if setting.target == "user":
            if user_id is None:
                return setting.default
            user_settings = await repo.get_user_settings(user_id)
            return getattr(user_settings, setting.key, setting.default) if user_settings else setting.default
        
        elif setting.target == "app":
            if setting.section is None:
                return setting.default
            getter_name = SECTION_REPO_MAP.get(setting.section, (None, None))[0]
            if getter_name is None:
                return setting.default
            
            getter = getattr(repo, getter_name, None)
            if getter is None:
                return setting.default
            
            value = await getter()
            if isinstance(value, dict):
                return value.get(setting.key, setting.default)
            else:
                return getattr(value, setting.key, setting.default) if value else setting.default
    except Exception as e:
        logger.warning(f"Failed to fetch setting {setting.key}: {e}")
        return setting.default


# ===========================
# Flow Generator (Main)
# ===========================

class SettingsFlowGenerator:
    """
    Generates MessageFlow states dynamically from settings schema.
    
    Usage:
        generator = SettingsFlowGenerator(
            user_update_handler=my_user_update_func,
            global_update_handler=my_global_update_func
        )
        generator.register_schema_states(flow, parent_main="main_menu", parent_admin="admin_menu")
    """
    
    def __init__(
        self,
        user_update_handler: Optional[Callable] = None,
        global_update_handler: Optional[Callable] = None,
    ):
        """
        Initialize generator with update handlers.
        
        Args:
            user_update_handler: async func(flow_state, api, user_id, **kwargs) -> bool
            global_update_handler: async func(flow_state, api, user_id, coro) -> bool
        """
        self.user_update_handler = user_update_handler
        self.global_update_handler = global_update_handler

    @staticmethod
    def _enum_uses_selection(setting: Setting) -> bool:
        behavior = (setting.enum_behavior or "").strip().lower()
        return behavior == "select"

    @staticmethod
    def _format_enum_value(setting: Setting, value: Any) -> str:
        if value is None:
            return ""
        text = str(value)
        if setting.key == "log_level":
            return text.upper()
        return text.replace("_", " ").title()

    @staticmethod
    def _subcategory_setting_keys(category_meta: SettingCategory) -> set[str]:
        return {
            key
            for subcategory in category_meta.subcategories
            for key in subcategory.setting_keys
        }

    @staticmethod
    def _subcategory_route_ids(category_meta: SettingCategory) -> set[str]:
        return {
            subcategory.custom_route_id
            for subcategory in category_meta.subcategories
            if subcategory.custom_route_id
        }

    def _direct_settings_for_scope(self, category_meta: SettingCategory, scope: str) -> list[Setting]:
        subcategory_keys = self._subcategory_setting_keys(category_meta)
        subcategory_routes = self._subcategory_route_ids(category_meta)
        direct_settings: list[Setting] = []
        for setting in category_meta.settings:
            if setting.key in subcategory_keys:
                continue
            if setting.custom_route_id in subcategory_routes:
                continue
            if scope == "main" and setting.target != "user":
                continue
            if scope == "admin" and setting.target != "app":
                continue
            direct_settings.append(setting)
        return direct_settings

    @staticmethod
    def _category_state_id(scope: str, category_key: str) -> str:
        return f"sd:cat:{scope}:{category_key}"

    @staticmethod
    def _subcategory_state_id(scope: str, category_key: str, subcategory_key: str) -> str:
        return f"sd:subcat:{scope}:{category_key}:{subcategory_key}"

    @staticmethod
    def _input_state_id(scope: str, category_key: str, setting_key: str) -> str:
        return f"sd:input:{scope}:{category_key}:{setting_key}"

    @staticmethod
    def _enum_select_state_id(
        scope: str,
        category_key: str,
        setting_key: str,
        subcategory_key: Optional[str] = None,
    ) -> str:
        if subcategory_key:
            return f"sd:enum_select:{scope}:{category_key}:{setting_key}:{subcategory_key}"
        return f"sd:enum_select:{scope}:{category_key}:{setting_key}"

    def register_schema_states(
        self,
        flow: MessageFlow,
        parent_main: Optional[str] = None,
        parent_admin: Optional[str] = None,
    ) -> None:
        """
        Register all schema-driven states onto a MessageFlow.
        
        For each category in CATEGORIES:
        - Creates two category view states (main + admin scope)
        - Creates number input states for numeric settings
        
        Special cases (custom_route_id) are NOT handled here - they should be
        added separately in the flow definition.
        
        Args:
            flow: MessageFlow instance to register states onto
            parent_main: Parent state ID for main menu categories (hierarchical navigation)
            parent_admin: Parent state ID for admin menu categories (hierarchical navigation)
        """
        for category_key, category_meta in CATEGORIES.items():
            self._register_category_pair(
                flow,
                category_key,
                category_meta,
                parent_main,
                parent_admin,
            )

            for subcategory in category_meta.subcategories:
                self._register_subcategory_pair(
                    flow,
                    category_key,
                    category_meta,
                    subcategory,
                )

            main_parent_state = self._category_state_id("main", category_key)
            admin_parent_state = self._category_state_id("admin", category_key)

            for setting in category_meta.settings:
                scope = "main" if setting.target == "user" else "admin"
                parent_state = main_parent_state if scope == "main" else admin_parent_state

                if setting.type == SettingType.NUMBER:
                    self._register_number_input_state(
                        flow,
                        scope,
                        category_key,
                        setting,
                        parent_state,
                    )

                if setting.type == SettingType.ENUM and self._enum_uses_selection(setting):
                    self._register_enum_select_state(
                        flow,
                        scope=scope,
                        category_key=category_key,
                        setting=setting,
                        parent_state=parent_state,
                    )

    def _register_number_input_state(
        self,
        flow: MessageFlow,
        scope: str,
        category_key: str,
        setting: Setting,
        parent_state: str,
    ) -> None:
        """
        Register a number input state for a numeric setting.
        
        Args:
            flow: MessageFlow instance to register onto
            scope: "main" or "admin" (for consistency, though used in state ID)
            category_key: Category key (e.g., "debts")
            setting: Setting definition with min_value, max_value, etc.
            parent_state: Parent state ID for back navigation
        """
        state_id = self._input_state_id(scope, category_key, setting.key)
        
        async def text_builder(flow_state, api, user_id):
            """Build the number input prompt text."""
            repo = api.conversation_manager.repo
            current_val = await _get_current_setting_value(repo, setting, user_id)
            
            min_val = setting.min_value or 0
            max_val = setting.max_value or 100
            label = setting.label or setting.key.replace("_", " ").title()
            
            return (
                f"🔢 **{label}**\n\n"
                f"Current value: **{current_val}**\n\n"
                f"Enter a new value ({min_val}-{max_val}):"
            )

        async def input_handler(input_text: str, flow_state, api, user_id) -> Optional[str]:
            """Handle number input: parse, validate, and update."""
            parser = IntegerParser()
            val = parser.parse(input_text)
            
            min_val = setting.min_value or 0
            max_val = setting.max_value or 100
            
            if val is None or val < min_val or val > max_val:
                await api.message_manager.send_text(
                    user_id,
                    f"❌ Invalid input. Please enter a number between {min_val} and {max_val}.",
                    vanish=True,
                    conv=True,
                    delete_after=3,
                )
                return state_id  # Stay on current state
            
            await self._apply_setting_value(flow_state, api, user_id, setting, val)
            
            return parent_state  # Return to parent category view

# Register the input state with a Back button keyboard so user can cancel without input
        async def kb_builder(flow_state, api, user_id):
            return [[ButtonCallback("◁ Back", CommonCallbacks.BACK)]]

        state = MessageDefinition(
            state_id=state_id,
            state_type=StateType.TEXT_INPUT,
            text_builder=text_builder,
            keyboard_builder=kb_builder,
            input_prompt=None,
            input_storage_key=state_id,
            input_timeout=120,
            on_input_received=input_handler,
            action=MessageAction.EDIT,
            back_button=CommonCallbacks.BACK,
            parent_state=parent_state,
        )
        flow.add_state(state)

    def _register_category_pair(
        self,
        flow: MessageFlow,
        category_key: str,
        category_meta: SettingCategory,
        parent_main: Optional[str],
        parent_admin: Optional[str],
    ) -> None:
        """
        Register a pair of category view states (main scope + admin scope).
        
        Example: for "logging" category, registers:
        - "sd:cat:main:logging" (shows user-target settings in logging)
        - "sd:cat:admin:logging" (shows app-target settings in logging)
        """
        # State for main menu (user-level settings)
        state_id_main = self._category_state_id("main", category_key)
        self._register_category_state(
            flow,
            state_id_main,
            category_key,
            category_meta,
            scope="main",
            parent_state=parent_main,
        )
        
        # State for admin menu (app-level settings)
        state_id_admin = self._category_state_id("admin", category_key)
        self._register_category_state(
            flow,
            state_id_admin,
            category_key,
            category_meta,
            scope="admin",
            parent_state=parent_admin,
        )

    def _register_category_state(
        self,
        flow: MessageFlow,
        state_id: str,
        category_key: str,
        category_meta: SettingCategory,
        scope: str,  # "main" or "admin"
        parent_state: Optional[str],
    ) -> None:
        """
        Register a single category view state.
        
        The state shows all settings in the category, filtered by scope.
        Tapping a setting routes based on type (toggle/number/enum or custom).
        """
        
        async def text_builder(flow_state, api, user_id):
            """Build the category header text with current values."""
            repo = api.conversation_manager.repo
            parts = [f"**{category_meta.label or category_key}**", ""]

            for setting in self._direct_settings_for_scope(category_meta, scope):
                icon = ICON_MAP.get(setting.key, "")
                label = f"{icon} {setting.label}" if icon else setting.label
                if setting.type == SettingType.CUSTOM:
                    parts.append(f"• {label}")
                else:
                    current_val = await _get_current_setting_value(repo, setting, user_id)
                    value_text = self._format_enum_value(setting, current_val) if setting.type == SettingType.ENUM else current_val
                    parts.append(f"• {label}: **{value_text}**")

                if setting.description:
                    parts.append(f"  {setting.description}")
                parts.append("")

            subcategories = self._get_subcategories_for_scope(category_meta, scope)
            for subcategory, _ in subcategories:
                icon = subcategory.icon or ICON_MAP.get(subcategory.key, "")
                label = f"{icon} {subcategory.label}" if icon else subcategory.label
                parts.append(f"• {label}")
                if subcategory.description:
                    parts.append(f"  {subcategory.description}")
                parts.append("")

            return "\n".join(parts)

        async def keyboard_builder(flow_state, api, user_id):
            """Build keyboard with buttons for each setting."""
            repo = api.conversation_manager.repo
            buttons: List[List[ButtonCallback]] = []

            for setting in self._direct_settings_for_scope(category_meta, scope):
                current_val = await _get_current_setting_value(repo, setting, user_id)
                icon = ICON_MAP.get(setting.key, "")
                label = f"{icon} {setting.label}" if icon else setting.label

                if setting.type == SettingType.CUSTOM:
                    cb = setting.custom_route_id
                    if cb:
                        buttons.append([ButtonCallback(label, cb)])
                    continue

                if setting.type == SettingType.TOGGLE:
                    cb_data = f"sd:toggle:{category_key}:{setting.key}"
                    tb = toggle_button(bool(current_val), label, cb_data)
                    buttons.append([tb])
                elif setting.type == SettingType.ENUM:
                    if self._enum_uses_selection(setting):
                        cb_data = self._enum_select_state_id(scope, category_key, setting.key)
                    else:
                        cb_data = f"sd:enum:{category_key}:{setting.key}"
                    buttons.append([ButtonCallback(f"{label}: {self._format_enum_value(setting, current_val)}", cb_data)])
                elif setting.type == SettingType.NUMBER:
                    cb_data = f"sd:number_edit:{category_key}:{setting.key}"
                    buttons.append([ButtonCallback(f"{label}: {current_val}", cb_data)])

            subcategories = self._get_subcategories_for_scope(category_meta, scope)
            if subcategories:
                for subcategory, sub_settings in subcategories:
                    icon = subcategory.icon or ICON_MAP.get(subcategory.key, "")
                    label = f"{icon} {subcategory.label}" if icon else subcategory.label
                    if subcategory.custom_route_id:
                        buttons.append([ButtonCallback(label, subcategory.custom_route_id)])
                    elif len(sub_settings) == 1 and sub_settings[0].type == SettingType.ENUM and self._enum_uses_selection(sub_settings[0]):
                        enum_state = self._enum_select_state_id(scope, category_key, sub_settings[0].key, subcategory.key)
                        buttons.append([ButtonCallback(label, enum_state)])
                    else:
                        buttons.append([
                            ButtonCallback(
                                label,
                                f"sd:subcat:{scope}:{category_key}:{subcategory.key}",
                            )
                        ])

                buttons.append([ButtonCallback("◁ Back", CommonCallbacks.BACK)])
                return buttons

            buttons.append([ButtonCallback("◁ Back", CommonCallbacks.BACK)])
            return buttons

        # Register the state
        state = make_state(
            state_id,
            text_builder=text_builder,
            keyboard_builder=keyboard_builder,
            action=MessageAction.EDIT,
            timeout=120,
            on_button_press=self._make_category_button_handler(category_key, category_meta, scope),
            back_button=CommonCallbacks.BACK,
            parent_state=parent_state,
        )
        flow.add_state(state)

    def _make_category_button_handler(self, category_key: str, category_meta: SettingCategory, scope: str):
        """
        Create a button press handler for a category view.
        
        Handles:
        - Toggle: flip the value
        - Enum: cycle to next option
        - Number: navigate to input state
        - Custom: route to custom_route_id
        """
        async def handler(data: str, flow_state, api, user_id) -> Optional[str]:
            # Parse button data format: "sd:toggle:category:key" etc.
            if not data:
                return None

            for setting in category_meta.settings:
                if scope == "main" and setting.target != "user":
                    continue
                if scope == "admin" and setting.target != "app":
                    continue
                if setting.type == SettingType.CUSTOM and setting.custom_route_id == data:
                    return setting.custom_route_id

            for subcategory, _ in self._get_subcategories_for_scope(category_meta, scope):
                if subcategory.custom_route_id == data:
                    return subcategory.custom_route_id

            if not data.startswith("sd:"):
                return None
            
            parts = data.split(":")
            if len(parts) < 4:
                return None

            action_type = parts[1]  # "toggle", "enum", "number_edit", custom id
            button_category = parts[2]
            setting_key = ":".join(parts[3:])  # In case key contains colons

            if action_type == "subcat":
                return data
            if action_type == "enum_select":
                return data
            
            setting = None
            for candidate in category_meta.settings:
                if candidate.key != setting_key:
                    continue
                if scope == "main" and candidate.target != "user":
                    continue
                if scope == "admin" and candidate.target != "app":
                    continue
                setting = candidate
                break

            if setting is None:
                return None
            
            if action_type == "toggle":
                return await self._handle_toggle(flow_state, api, user_id, setting)
            elif action_type == "enum":
                return await self._handle_enum(flow_state, api, user_id, setting)
            elif action_type == "number_edit":
                return f"sd:input:{scope}:{button_category}:{setting_key}"
            
            return None
        
        return handler

    def _get_subcategories_for_scope(self, category_meta: SettingCategory, scope: str):
        setting_map = {setting.key: setting for setting in category_meta.settings}
        subcategories = []

        for subcategory in category_meta.subcategories:
            settings = []
            for key in subcategory.setting_keys:
                setting = setting_map.get(key)
                if setting is None:
                    continue
                if scope == "main" and setting.target != "user":
                    continue
                if scope == "admin" and setting.target != "app":
                    continue
                settings.append(setting)

            if subcategory.custom_route_id or settings:
                subcategories.append((subcategory, settings))

        return subcategories

    def _register_subcategory_pair(
        self,
        flow: MessageFlow,
        category_key: str,
        category_meta: SettingCategory,
        subcategory,
    ) -> None:
        self._register_subcategory_state(
            flow,
            scope="main",
            category_key=category_key,
            category_meta=category_meta,
            subcategory=subcategory,
        )
        self._register_subcategory_state(
            flow,
            scope="admin",
            category_key=category_key,
            category_meta=category_meta,
            subcategory=subcategory,
        )

    def _register_subcategory_state(
        self,
        flow: MessageFlow,
        scope: str,
        category_key: str,
        category_meta: SettingCategory,
        subcategory,
    ) -> None:
        if subcategory.custom_route_id:
            return

        subcategory_settings = next(
            (
                settings
                for sub_meta, settings in self._get_subcategories_for_scope(category_meta, scope)
                if sub_meta.key == subcategory.key
            ),
            None,
        )

        if not subcategory_settings:
            return

        state_id = self._subcategory_state_id(scope, category_key, subcategory.key)

        for setting in subcategory_settings:
            if setting.type == SettingType.ENUM and self._enum_uses_selection(setting):
                self._register_enum_select_state(
                    flow,
                    scope=scope,
                    category_key=category_key,
                    setting=setting,
                    parent_state=state_id,
                    subcategory_key=subcategory.key,
                )

        async def text_builder(flow_state, api, user_id):
            repo = api.conversation_manager.repo
            icon = subcategory.icon or ICON_MAP.get(subcategory.key, "")
            label = f"{icon} {subcategory.label}" if icon else subcategory.label
            parts = [f"**{label}**", ""]
            if subcategory.description:
                parts.append(subcategory.description)
                parts.append("")

            for setting in subcategory_settings:
                icon_setting = ICON_MAP.get(setting.key, "")
                setting_label = f"{icon_setting} {setting.label}" if icon_setting else setting.label
                if setting.type == SettingType.CUSTOM:
                    parts.append(f"• {setting_label}")
                else:
                    current_val = await _get_current_setting_value(repo, setting, user_id)
                    value_text = self._format_enum_value(setting, current_val) if setting.type == SettingType.ENUM else current_val
                    parts.append(f"• {setting_label}: **{value_text}**")
                if setting.description:
                    parts.append(f"  {setting.description}")
                parts.append("")
            return "\n".join(parts)

        async def keyboard_builder(flow_state, api, user_id):
            repo = api.conversation_manager.repo
            buttons: List[List[ButtonCallback]] = []
            use_compact = subcategory.button_style == "compact_toggle"
            use_grid = subcategory.layout == "grid_2"

            row: List[ButtonCallback] = []

            for setting in subcategory_settings:
                icon_setting = ICON_MAP.get(setting.key, "")
                label = f"{icon_setting} {setting.label}" if icon_setting else setting.label
                compact_label = setting.label

                if setting.type == SettingType.CUSTOM:
                    cb = setting.custom_route_id
                    if cb:
                        buttons.append([ButtonCallback(label, cb)])
                    continue

                if setting.type == SettingType.TOGGLE:
                    current_val = await _get_current_setting_value(repo, setting, user_id)
                    cb_data = f"sd:toggle:{category_key}:{setting.key}"
                    if use_compact:
                        tb = compact_toggle_button(bool(current_val), compact_label, cb_data)
                    else:
                        tb = toggle_button(bool(current_val), label, cb_data)

                    if use_grid:
                        row.append(tb)
                        if len(row) == 2:
                            buttons.append(row)
                            row = []
                    else:
                        buttons.append([tb])
                    continue

                if use_grid and row:
                    buttons.append(row)
                    row = []

                if setting.type == SettingType.ENUM:
                    current_val = await _get_current_setting_value(repo, setting, user_id)
                    if self._enum_uses_selection(setting):
                        cb_data = self._enum_select_state_id(scope, category_key, setting.key, subcategory.key)
                    else:
                        cb_data = f"sd:enum:{category_key}:{setting.key}"
                    buttons.append([ButtonCallback(f"{label}: {self._format_enum_value(setting, current_val)}", cb_data)])
                elif setting.type == SettingType.NUMBER:
                    current_val = await _get_current_setting_value(repo, setting, user_id)
                    cb_data = f"sd:number_edit:{category_key}:{setting.key}"
                    buttons.append([ButtonCallback(f"{label}: {current_val}", cb_data)])

            if row:
                buttons.append(row)

            buttons.append([ButtonCallback("◁ Back", CommonCallbacks.BACK)])
            return buttons

        state = make_state(
            state_id,
            text_builder=text_builder,
            keyboard_builder=keyboard_builder,
            action=MessageAction.EDIT,
            timeout=120,
            on_button_press=self._make_category_button_handler(category_key, category_meta, scope),
            back_button=CommonCallbacks.BACK,
            parent_state=f"sd:cat:{scope}:{category_key}",
        )
        flow.add_state(state)

    def _register_enum_select_state(
        self,
        flow: MessageFlow,
        scope: str,
        category_key: str,
        setting: Setting,
        parent_state: str,
        subcategory_key: Optional[str] = None,
    ) -> None:
        state_id = self._enum_select_state_id(scope, category_key, setting.key, subcategory_key)

        async def text_builder(flow_state, api, user_id):
            repo = api.conversation_manager.repo
            current_val = await _get_current_setting_value(repo, setting, user_id)
            icon = ICON_MAP.get(setting.key, "")
            label = f"{icon} {setting.label}" if icon else setting.label

            value_text = self._format_enum_value(setting, current_val) if setting.type == SettingType.ENUM else current_val

            parts = [f"**{label}**", ""]
            if setting.description:
                parts.append(setting.description)
                parts.append("")
            parts.append(f"Current value: **{value_text}**")
            parts.append("")
            parts.append("Select a value:")
            return "\n".join(parts)

        async def keyboard_builder(flow_state, api, user_id):
            repo = api.conversation_manager.repo
            current_val = await _get_current_setting_value(repo, setting, user_id)
            buttons: List[List[ButtonCallback]] = []

            # Layout options in a configurable column grid
            cols = getattr(setting, "enum_columns", None) or 1
            row: List[ButtonCallback] = []
            for option in setting.options or []:
                prefix = "✅" if option == current_val else ""
                # Add colored dot for logging levels
                dot = ""
                if getattr(setting, "key", "") == "log_level":
                    dot = LOG_LEVEL_STATE_ICON.get(option, "")
                btn_text = f"{prefix} {self._format_enum_value(setting, option)} {dot}".strip()
                btn = ButtonCallback(btn_text, f"sd:enum_pick:{category_key}:{setting.key}:{option}")
                row.append(btn)
                if len(row) >= int(cols):
                    buttons.append(row)
                    row = []

            if row:
                buttons.append(row)

            buttons.append([ButtonCallback("◁ Back", CommonCallbacks.BACK)])
            return buttons

        async def handle_button(data: str, flow_state, api, user_id) -> Optional[str]:
            if not data or not data.startswith("sd:enum_pick:"):
                return None

            parts = data.split(":")
            if len(parts) < 5:
                return None

            pick_category = parts[2]
            pick_key = parts[3]
            pick_value = ":".join(parts[4:])

            if pick_category != category_key or pick_key != setting.key:
                return None

            await self._apply_enum_value(flow_state, api, user_id, setting, pick_value)
            return parent_state

        state = make_state(
            state_id,
            text_builder=text_builder,
            keyboard_builder=keyboard_builder,
            action=MessageAction.EDIT,
            timeout=120,
            on_button_press=handle_button,
            back_button=CommonCallbacks.BACK,
            parent_state=parent_state,
        )
        flow.add_state(state)

    async def _handle_toggle(self, flow_state, api, user_id, setting: Setting) -> Optional[str]:
        """Handle toggle button press: flip value and save."""
        repo = api.conversation_manager.repo
        current = await _get_current_setting_value(repo, setting, user_id)
        new_value = not bool(current)

        await self._apply_setting_value(flow_state, api, user_id, setting, new_value)
        
        return None  # Stay on current state

    async def _handle_enum(self, flow_state, api, user_id, setting: Setting) -> Optional[str]:
        """Handle enum button press: cycle to next option."""
        if not setting.options:
            return None
        repo = api.conversation_manager.repo
        current = await _get_current_setting_value(repo, setting, user_id)
        
        try:
            idx = setting.options.index(current)
            next_idx = (idx + 1) % len(setting.options)
            new_value = setting.options[next_idx]
        except (ValueError, IndexError):
            new_value = setting.options[0]
        await self._apply_enum_value(flow_state, api, user_id, setting, new_value)
        
        return None  # Stay on current state

    async def _apply_enum_value(self, flow_state, api, user_id, setting: Setting, new_value: str) -> None:
        await self._apply_setting_value(flow_state, api, user_id, setting, new_value)

    async def _apply_setting_value(self, flow_state, api, user_id, setting: Setting, value: Any) -> None:
        if setting.target == "user":
            if self.user_update_handler is not None:
                await self.user_update_handler(flow_state, api, user_id, **{setting.key: value})
            return

        if setting.section is None:
            return

        updater_name = SECTION_REPO_MAP.get(setting.section, (None, None))[1]
        if not updater_name or self.global_update_handler is None:
            return

        updater = getattr(api.conversation_manager.repo, updater_name, None)
        if updater is None:
            return

        coro = updater(**{setting.key: value})
        await self.global_update_handler(flow_state, api, user_id, coro)


__all__ = [
    "SettingsFlowGenerator",
    "SECTION_REPO_MAP",
]
