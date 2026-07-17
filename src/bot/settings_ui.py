"""Settings UI helpers for MessageFlow-based settings menus."""

import asyncio
from typing import Any, Dict, List, Optional

from .message_flow import ButtonCallback
from .message_flow_helpers import compact_toggle_button, toggle_button
from .settings_schema import ADMIN_MENU, CATEGORIES, ICON_MAP, MAIN_MENU, SettingType


InlineKeyboard = List[List[ButtonCallback]]


class SettingsUi:
    """Builds settings menu text and keyboards for MessageFlow screens."""
    
    def __init__(self, api):
        """
        Initialize the Settings UI helper.
        
        Args:
            api: Reference to TelethonAPI instance for message operations
        """
        self.api = api

    # === Button Icons ===
    ICON_BACK = "◁"
    ICON_CANCEL = " ✖"

    def _menu_button_label(self, category_key: str) -> str:
        category = CATEGORIES.get(category_key)
        if category is None:
            icon = ICON_MAP.get(category_key, "")
            return f"{icon} {category_key.title()}".strip()

        icon = category.icon or ICON_MAP.get(category_key, "")
        return f"{icon} {category.label}".strip()

    def _format_enum_value(self, setting, value: Any) -> str:
        if value is None:
            return ""
        text = str(value)
        if setting.key == "log_level":
            return text.upper()
        return text.replace("_", " ").title()

    def _split_direct_settings(self, meta, targets):
        subcategory_keys = {
            key
            for subcategory in meta.subcategories
            for key in subcategory.setting_keys
        }
        subcategory_routes = {
            subcategory.custom_route_id
            for subcategory in meta.subcategories
            if subcategory.custom_route_id
        }
        return [
            setting
            for setting in meta.settings
            if setting.target in targets
            and setting.key not in subcategory_keys
            and setting.custom_route_id not in subcategory_routes
        ]

    def _build_menu_keyboard(
        self,
        menu,
        extra_buttons: Optional[List[ButtonCallback]] = None,
        *,
        include_back: bool = True,
    ) -> InlineKeyboard:
        keyboard: InlineKeyboard = []
        for category_key in menu.categories:
            keyboard.append([ButtonCallback(self._menu_button_label(category_key), category_key)])

        if extra_buttons:
            for button in extra_buttons:
                keyboard.append([button])

        if include_back:
            keyboard.append([ButtonCallback(f"{self.ICON_BACK} Back", "back")])
        return keyboard
    
    # === Menu Generators ===
    
    def get_main_menu_text(self) -> str:
        """Generate the main settings menu text."""
        return (
            f"{MAIN_MENU.icon or '⚙️'} **{MAIN_MENU.label}**\n\n"
            f"{MAIN_MENU.description or 'Select a category to adjust:'}"
        )
    
    def get_main_menu_keyboard(self, *, include_admin: bool = True) -> InlineKeyboard:
        """Generate the main settings menu keyboard."""
        keyboard: InlineKeyboard = self._build_menu_keyboard(MAIN_MENU, include_back=False)

        if include_admin:
            keyboard.append([ButtonCallback(f"{ADMIN_MENU.icon or '🛠️'} {ADMIN_MENU.label}", "admin")])

        keyboard.append([ButtonCallback("✅ Done", "done")])
        return keyboard

    # === Schema-driven helpers ===
    def _get_setting_value(self, s, user_settings=None, app_settings=None):
        try:
            if s.target == "user":
                if user_settings is None:
                    return s.default
                return getattr(user_settings, s.key, s.default)
            else:
                if app_settings is None:
                    return s.default
                if isinstance(app_settings, dict):
                    return app_settings.get(s.key, s.default)
                return getattr(app_settings, s.key, s.default)
        except Exception:
            return s.default

    def get_schema_category_text(self, category_key: str, user_settings=None, app_settings=None, targets=("user", "app")) -> str:
        meta = CATEGORIES.get(category_key)
        if not meta:
            return "**Missing Settings**\n\nNo settings configured."
        parts = [f"**{meta.label}**", ""]
        direct_settings = self._split_direct_settings(meta, targets)

        for setting in direct_settings:
            cur = self._get_setting_value(setting, user_settings, app_settings)
            icon = ICON_MAP.get(setting.key, "")
            label = f"{icon} {setting.label}" if icon else setting.label
            if setting.type == SettingType.CUSTOM:
                parts.append(f"• {label}")
            else:
                value_text = self._format_enum_value(setting, cur) if setting.type == SettingType.ENUM else cur
                parts.append(f"• {label}: **{value_text}**")
            if getattr(setting, "description", None):
                parts.append(f"   _{setting.description}_")
            parts.append("")

        if meta.subcategories:
            for subcategory in meta.subcategories:
                icon = subcategory.icon or ICON_MAP.get(subcategory.key, "")
                label = f"{icon} {subcategory.label}" if icon else subcategory.label
                parts.append(f"• {label}")
                if subcategory.description:
                    parts.append(f"   _{subcategory.description}_")
                parts.append("")
        return "\n".join(parts)

    def get_schema_category_keyboard(self, category_key: str, user_settings=None, app_settings=None, targets=("user", "app")) -> InlineKeyboard:
        meta = CATEGORIES.get(category_key)
        buttons: InlineKeyboard = []
        if not meta:
            buttons.append([ButtonCallback(f"{self.ICON_BACK} Back", "back")])
            return buttons

        if getattr(meta, "subcategories", None):
            if "user" in targets and "app" not in targets:
                scope = "main"
            else:
                scope = "admin"
            direct_settings = self._split_direct_settings(meta, targets)
            for setting in direct_settings:
                cur = self._get_setting_value(setting, user_settings, app_settings)
                icon = ICON_MAP.get(setting.key, "")
                label_base = f"{icon} {setting.label}" if icon else setting.label
                if setting.type == SettingType.TOGGLE:
                    callback_data = f"sd:toggle:{category_key}:{setting.key}"
                    tb = toggle_button(bool(cur), label_base, callback_data)
                    buttons.append([ButtonCallback(tb.text, tb.callback_data)])
                elif setting.type == SettingType.ENUM:
                    callback_data = f"sd:enum_select:{scope}:{category_key}:{setting.key}"
                    buttons.append([ButtonCallback(f"{label_base}: {self._format_enum_value(setting, cur)}", callback_data)])
                elif setting.type == SettingType.NUMBER:
                    callback_data = f"sd:number_edit:{category_key}:{setting.key}"
                    buttons.append([ButtonCallback(f"{label_base}: {cur}", callback_data)])
                elif setting.type == SettingType.CUSTOM and setting.custom_route_id:
                    buttons.append([ButtonCallback(label_base, setting.custom_route_id)])
            for subcategory in meta.subcategories:
                icon = subcategory.icon or ICON_MAP.get(subcategory.key, "")
                label = f"{icon} {subcategory.label}" if icon else subcategory.label
                callback = subcategory.custom_route_id or f"sd:subcat:{scope}:{category_key}:{subcategory.key}"
                buttons.append([ButtonCallback(label, callback)])
            buttons.append([ButtonCallback(f"{self.ICON_BACK} Back", "back")])
            return buttons

        for s in meta.settings:
            if s.target not in targets:
                continue
            cur = self._get_setting_value(s, user_settings, app_settings)
            icon = ICON_MAP.get(s.key, "")
            label_base = f"{icon} {s.label}" if icon else s.label
            if s.type == SettingType.TOGGLE:
                callback_data = f"sd:toggle:{category_key}:{s.key}"
                tb = toggle_button(bool(cur), label_base, callback_data)
                buttons.append([ButtonCallback(tb.text, tb.callback_data)])
            elif s.type == SettingType.ENUM:
                callback_data = f"sd:enum:{category_key}:{s.key}"
                buttons.append([ButtonCallback(f"{label_base}: {self._format_enum_value(s, cur)}", callback_data)])
            elif s.type == SettingType.NUMBER:
                callback_data = f"sd:number_edit:{category_key}:{s.key}"
                buttons.append([ButtonCallback(f"{label_base}: {cur}", callback_data)])
            elif s.type == SettingType.CUSTOM and s.custom_route_id:
                buttons.append([ButtonCallback(label_base, s.custom_route_id)])

        buttons.append([ButtonCallback(f"{self.ICON_BACK} Back", "back")])
        return buttons
    
    def get_admin_submenu_text(self) -> str:
        """
        Generate the admin settings submenu text.
        
        Returns:
            Formatted text for the admin submenu
        """
        return (
            f"{ADMIN_MENU.icon or '🛠️'} **{ADMIN_MENU.label}**\n\n"
            f"{ADMIN_MENU.description or 'Select a category to configure:'}"
        )
    
    def get_admin_submenu_keyboard(self) -> InlineKeyboard:
        """Generate the admin settings submenu keyboard."""
        extra_buttons = [ButtonCallback("🔐 Password", "registration_password")]
        return self._build_menu_keyboard(ADMIN_MENU, extra_buttons=extra_buttons)

    def get_registration_password_submenu_text(self) -> str:
        """
        Generate the registration password admin submenu text.
        """
        return (
            "🔐 **Registration Password**\n\n"
            "The registration password is needed to register as a new user.\n\n"
            "Use the button below to change the current registration password."
        )

    def get_registration_password_submenu_keyboard(self) -> InlineKeyboard:
        """Generate the registration password submenu keyboard."""
        return [
            [ButtonCallback("🔑 Change Registration Password", "change_password")],
            [ButtonCallback(f"{self.ICON_BACK} Back", "back")],
        ]

    def get_logging_format_text(self, log_settings: Dict) -> str:
        """
        Generate the logging format configuration text.
        
        Args:
            log_settings: Dictionary with log_show_time, log_show_caller, log_show_class
            
        Returns:
            Formatted text for the logging format screen
        """
        # Generate example log based on current settings
        example_parts = []
        if log_settings.get("log_show_time", True):
            example_parts.append("11:51:25")
        example_parts.append("INFO")
        if log_settings.get("log_show_caller", True):
            example_parts.append("[beanie_repo:create_user]")
        if log_settings.get("log_show_class", True):
            example_parts.append("[BeanieRepository]")
        example_parts.append("Created user: Lukas")
        example_log = " - ".join(example_parts)
        
        return (
            "🎨 **Logging Format**\n\n"
            "Toggle format components:\n\n"
            f"**Example Preview:**\n"
            f"`{example_log}`\n\n"
            "Click the buttons below to toggle each component:"
        )
    
    def get_logging_format_keyboard(self, log_settings: Dict) -> InlineKeyboard:
        """
        Generate the logging format keyboard with toggle buttons.
        
        Args:
            log_settings: Dictionary with log_show_time, log_show_caller, log_show_class
            
        Returns:
            Keyboard with toggle buttons
        """
        time_enabled = bool(log_settings.get("log_show_time", True))
        caller_enabled = bool(log_settings.get("log_show_caller", True))
        class_enabled = bool(log_settings.get("log_show_class", True))

        bt = compact_toggle_button(time_enabled, "Time", "toggle_time")
        bc = compact_toggle_button(caller_enabled, "Caller", "toggle_caller")
        bj = compact_toggle_button(class_enabled, "Class", "toggle_class")

        return [
            [ButtonCallback(bt.text, bt.callback_data), ButtonCallback(bc.text, bc.callback_data), ButtonCallback(bj.text, bj.callback_data)],
            [ButtonCallback(f"{self.ICON_BACK} Back", "back")]
        ]
    
    # No conversation-specific input handlers remain; numeric/text input
    # is handled via MessageFlow states in `settings_flow.py`.
    
    async def show_brief_confirmation(self, message: Any, text: str, duration: float = 1.0):
        """
        Show a brief confirmation message by editing an existing message.
        
        Args:
            message: Message to edit
            text: Confirmation text to show
            duration: How long to show the message (seconds)
        """
        await self.api.message_manager.edit_message(
            message,
            text,
            buttons=None
        )
        await asyncio.sleep(duration)
