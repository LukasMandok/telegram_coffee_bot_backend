"""
Settings Manager for handling user settings UI and workflows.

This module provides a centralized way to:
- manage user settings conversations and generate consistent menus
- initialize application settings from the database on startup
"""

import asyncio
import logging
from typing import Optional, Tuple, Any, Dict, List
from .message_flow import ButtonCallback
from ..dependencies.dependencies import get_repo
from ..common.log import log_settings
from ..common.log import LOG_STATE_ICON, format_log_state
from .message_flow_helpers import toggle_button

logger = logging.getLogger(__name__)


InlineKeyboard = List[List[ButtonCallback]]


class SettingsManager:
    """
    Manages user settings UI generation and common input workflows.
    
    This class centralizes all settings-related menu generation and input handling
    to avoid code duplication and ensure consistent UX.
    """
    
    def __init__(self, api):
        """
        Initialize the SettingsManager.
        
        Args:
            api: Reference to TelethonAPI instance for message operations
        """
        self.api = api

    # === Application settings initialization ===
    @classmethod
    async def initialize_log_settings_from_db(cls) -> None:
        """
        Load logging settings from the database and apply them to runtime.

        This should be called on application startup, after the database
        connection is established.
        """
        try:
            repo = get_repo()
            db_log_settings = await repo.get_log_settings()

            if db_log_settings:
                # Update runtime settings
                log_settings.show_time = db_log_settings.get("log_show_time", True)
                log_settings.show_caller = db_log_settings.get("log_show_caller", True)
                log_settings.show_class = db_log_settings.get("log_show_class", True)
                log_settings.level = db_log_settings.get("log_level", "INFO")

                # Module overrides are enforced by a dynamic filter.
                log_settings.module_overrides = dict(db_log_settings.get("log_module_overrides", {}) or {})

                logger.info(
                    f"Initialized log settings: level={log_settings.level}, "
                    f"time={log_settings.show_time}, caller={log_settings.show_caller}, "
                    f"class={log_settings.show_class}"
                )
            else:
                logger.warning("No log settings found in database, using defaults")
        except Exception as e:
            logger.error(f"Failed to initialize log settings: {str(e)}", exc_info=e)
    
    # === Button Icons ===
    ICON_BACK = "◁"
    ICON_CANCEL = " ✖"
    
    # === Menu Generators ===
    
    def get_main_menu_text(self) -> str:
        """Generate the main settings menu text."""
        return (
            "⚙️ **Your Settings**\n\n"
            "Select a category to adjust:"
        )
    
    def get_main_menu_keyboard(self, *, include_admin: bool = True) -> InlineKeyboard:
        """Generate the main settings menu keyboard."""
        keyboard: InlineKeyboard = [
            [ButtonCallback("📋 Ordering Settings", "ordering")],
            [ButtonCallback("💬 Vanishing Messages", "vanishing")],
            [ButtonCallback("🔔 Notifications", "user_notifications")],
        ]

        if include_admin:
            keyboard.append([ButtonCallback("🔧 Administration", "admin")])

        keyboard.append([ButtonCallback("✅ Done", "done")])
        return keyboard
    
    def get_ordering_submenu_text(self, settings) -> str:
        """
        Generate the ordering settings submenu text.
        
        Args:
            settings: UserSettings object
            
        Returns:
            Formatted text for the ordering submenu
        """
        return (
            "📋 **Ordering Settings**\n\n"
            f"**Group Page Size:** {settings.group_page_size} users per page\n"
            f"**Group Sorting:** {settings.group_sort_by.title()}\n\n"
            "Select a setting to adjust:"
        )
    
    def get_ordering_submenu_keyboard(self) -> InlineKeyboard:
        """Generate the ordering settings submenu keyboard."""
        return [
            [ButtonCallback("📄 Group Page Size", "page_size")],
            [ButtonCallback("🔤 Group Sorting", "sorting")],
            [ButtonCallback(f"{self.ICON_BACK} Back", "back")]
        ]
    
    def get_vanishing_submenu_text(self, settings) -> str:
        """
        Generate the vanishing messages submenu text.
        
        Args:
            settings: UserSettings object
            
        Returns:
            Formatted text for the vanishing submenu
        """
        vanish_status = "✅ On" if settings.vanishing_enabled else "❌ Off"
        
        return (
            "💬 **Vanishing Messages**\n\n"
            f"**Status:** {vanish_status}\n"
            f"**Vanish After:** {settings.vanishing_threshold} messages/conversations\n\n"
            "Vanishing messages automatically clean up old messages to keep your chat tidy.\n\n"
            "Select a setting to adjust:"
        )
    
    def get_vanishing_submenu_keyboard(self, settings=None) -> InlineKeyboard:
        """Generate the vanishing messages submenu keyboard.

        `settings` is optional; when provided the button reflects the current state.
        """
        enabled = bool(getattr(settings, "vanishing_enabled", False)) if settings is not None else False
        tb = toggle_button(enabled, "Vanishing Messages", "toggle")
        return [
            [ButtonCallback(tb.text, tb.callback_data)],
            [ButtonCallback("🔢 Vanish Threshold", "threshold")],
            [ButtonCallback(f"{self.ICON_BACK} Back", "back")]
        ]
    
    def get_user_notifications_submenu_text(self, user_settings, notification_settings: Dict) -> str:
        """
        Generate the user notification preference submenu text.
        
        Args:
            user_settings: UserSettings object
            notification_settings: Dictionary with app-wide notification settings
            
        Returns:
            Formatted text for the user notifications submenu
        """
        app_enabled = bool(notification_settings.get("notifications_enabled", True))
        app_silent = bool(notification_settings.get("notifications_silent", False))

        user_enabled = bool(user_settings.notifications_enabled)
        user_silent = bool(user_settings.notifications_silent)

        def _status(on: bool) -> str:
            return "✅ On" if on else "❌ Off"

        effective_enabled = app_enabled and user_enabled
        effective_silent = app_silent or user_silent

        silent_effective_label = _status(effective_silent) if effective_enabled else "⏸ N/A"

        # Keep this menu compact: show current state + only the relevant override hint.
        lines = [
            "🔔 **Notifications**",
            "",
            "**Global (admin):**",
            f"• **Enabled:** {_status(app_enabled)}",
            f"• **Silent:** {_status(app_silent)}",
            "",
            "**You:**",
            f"• **Enabled:** {_status(user_enabled)}",
            f"• **Silent:** {_status(user_silent)}",
            "",
            "**Effective for you:**",
            f"• **Enabled:** {_status(effective_enabled)}",
            f"• **Silent:** {silent_effective_label}",
            "",
        ]

        if not app_enabled:
            lines.append("⚠️ Global notifications are OFF → your **Enabled** toggle has no effect.")
        elif not user_enabled:
            lines.append("ℹ️ Notifications are OFF for you → silent mode doesn’t matter.")
        elif app_silent:
            lines.append("ℹ️ Global silent is ON → your **Silent** toggle has no effect.")

        lines.append("\nToggle below:")
        return "\n".join(lines)
    
    def get_user_notifications_submenu_keyboard(self, user_settings=None, notification_settings=None) -> InlineKeyboard:
        """Generate the user notification preference submenu keyboard.

        Optional `user_settings` may be provided so the buttons reflect current values.
        """
        user_enabled = bool(getattr(user_settings, "notifications_enabled", True)) if user_settings is not None else True
        user_silent = bool(getattr(user_settings, "notifications_silent", False)) if user_settings is not None else False
        app_enabled = bool(notification_settings.get("notifications_enabled", True)) if notification_settings is not None else True

        # Use callback IDs expected by the conversation handlers (user-specific)
        notif_btn = toggle_button(user_enabled, "Notifications", "toggle_user_notifications")

        # If global notifications are disabled, indicate Silent is N/A; otherwise render toggle
        if not app_enabled:
            silent_row = [ButtonCallback("⏸ Silent: N/A", "toggle_user_silent")]
        else:
            silent_btn = toggle_button(user_silent, "Silent", "toggle_user_silent")
            silent_row = [ButtonCallback(silent_btn.text, silent_btn.callback_data)]

        return [
            [ButtonCallback(notif_btn.text, notif_btn.callback_data)],
            silent_row,
            [ButtonCallback(f"{self.ICON_BACK} Back", "back")]
        ]
    
    def get_sorting_options_text(self, settings) -> str:
        """
        Generate the sorting options text.
        
        Args:
            settings: UserSettings object
            
        Returns:
            Formatted text for sorting options
        """
        return (
            "🔤 **Group Sorting**\n\n"
            "This setting controls how users are sorted when selecting a group for a coffee order.\n\n"
            f"**Current setting:** {settings.group_sort_by.title()}\n\n"
            "**Options:**\n"
            "• **Alphabetical** - Sort users by name (A-Z)\n"
            "• **Coffee Count** - Sort by number of coffees ordered (highest first, with alphabetical tiebreaker)\n\n"
            "Choose your preferred sorting:"
        )
    
    def get_sorting_options_keyboard(self) -> InlineKeyboard:
        """Generate the sorting options keyboard."""
        return [
            [ButtonCallback("🔤 Alphabetical", "alphabetical")],
            [ButtonCallback("☕ Coffee Count", "coffee_count")],
            [ButtonCallback(f"{self.ICON_BACK} Back", "back")]
        ]
    
    def get_cancel_keyboard(self) -> InlineKeyboard:
        """Generate a simple back button keyboard."""
        return [
            [ButtonCallback(f"{self.ICON_BACK} Back", "back")]
        ]
    
    def get_admin_submenu_text(self) -> str:
        """
        Generate the admin settings submenu text.
        
        Returns:
            Formatted text for the admin submenu
        """
        return (
            "🔧 **Administration Settings**\n\n"
            "Select a category to configure:"
        )
    
    def get_admin_submenu_keyboard(self) -> InlineKeyboard:
        """Generate the admin settings submenu keyboard."""
        return [
            [ButtonCallback("📊 Logging", "logging")],
            [ButtonCallback("🔔 Notifications", "notifications")],
            [ButtonCallback("💳 Debts", "debts")],
            [ButtonCallback("📄 Google Sheets", "gsheet")],
            [ButtonCallback("🔐 Password", "registration_password")],
            [ButtonCallback("📸 Snapshots", "snapshots")],
            [ButtonCallback(f"{self.ICON_BACK} Back", "back")]
        ]

    def get_registration_password_submenu_text(self) -> str:
        """
        Generate the registration password admin submenu text.
        """
        return (
            "🔐 **Registration Password**\n\n"
            "The registration password controls who can register new users.\n\n"
            "Use the button below to change the current registration password."
        )

    def get_registration_password_submenu_keyboard(self) -> InlineKeyboard:
        """Generate the registration password submenu keyboard."""
        return [
            [ButtonCallback("🔑 Change Registration Password", "change_password")],
            [ButtonCallback(f"{self.ICON_BACK} Back", "back")],
        ]

    def get_snapshots_submenu_text(self, snapshot_settings) -> str:
        keep_last = int(getattr(snapshot_settings, "keep_last", 10))

        def _status(value: bool) -> str:
            return "✅ On" if bool(value) else "❌ Off"

        return (
            "📸 **Snapshot Settings (App-Wide)**\n\n"
            f"**Keep last snapshots:** {keep_last}\n\n"
            "**Active snapshot creation points:**\n"
            f"• **Card closed:** {_status(getattr(snapshot_settings, 'card_closed', True))}\n"
            f"• **Session completed:** {_status(getattr(snapshot_settings, 'session_completed', True))}\n"
            f"• **Quick order:** {_status(getattr(snapshot_settings, 'quick_order', False))}\n"
            f"• **Card created:** {_status(getattr(snapshot_settings, 'card_created', True))}\n\n"
            "Select a setting to adjust:"
        )

    def get_snapshots_submenu_keyboard(self, snapshot_settings) -> InlineKeyboard:
        return [
            [ButtonCallback("🔢 Set Keep Last", "set_keep_last")],
            [ButtonCallback("⚙ Snapshot Creation Points", "creation_points")],
            [ButtonCallback(f"{self.ICON_BACK} Back", "back")],
        ]

    def get_snapshots_creation_points_submenu_text(self, snapshot_settings) -> str:
        def _status(value: bool) -> str:
            return "✅ On" if bool(value) else "❌ Off"

        return (
            "📸 **Snapshot Creation Points**\n\n"
            "Toggle which actions automatically create snapshots.\n\n"
            f"• **Card closed:** {_status(getattr(snapshot_settings, 'card_closed', True))}\n"
            f"• **Session completed:** {_status(getattr(snapshot_settings, 'session_completed', True))}\n"
            f"• **Quick order:** {_status(getattr(snapshot_settings, 'quick_order', False))}\n"
            f"• **Card created:** {_status(getattr(snapshot_settings, 'card_created', True))}\n\n"
            "Tap a button to toggle:"
        )

    def get_snapshots_creation_points_submenu_keyboard(self, snapshot_settings) -> InlineKeyboard:
        card_closed = bool(getattr(snapshot_settings, "card_closed", True))
        session_completed = bool(getattr(snapshot_settings, "session_completed", True))
        quick_order = bool(getattr(snapshot_settings, "quick_order", False))
        card_created = bool(getattr(snapshot_settings, "card_created", True))
        b1 = toggle_button(card_closed, "Card Closed", "toggle_card_closed")
        b2 = toggle_button(session_completed, "Session Completed", "toggle_session_completed")
        b3 = toggle_button(quick_order, "Quick Order", "toggle_quick_order")
        b4 = toggle_button(card_created, "Card Created", "toggle_card_created")

        return [
            [ButtonCallback(b1.text, b1.callback_data), ButtonCallback(b2.text, b2.callback_data)],
            [ButtonCallback(b3.text, b3.callback_data), ButtonCallback(b4.text, b4.callback_data)],
            [ButtonCallback(f"{self.ICON_BACK} Back", "back")],
        ]

    def get_debts_submenu_text(self, debt_settings) -> str:
        """Generate the debts settings submenu text (short overview)."""
        return (
            "💳 **Debt Settings (App-Wide)**\n\n"
            "• **Debt Correction:** distributes the cost of remaining coffees among qualifying consumers (method & threshold).\n"
            "• **Creditor Royalty:** optionally exclude the purchaser from corrections and/or grant them free coffees that others cover.\n\n"
            "Select an option below to edit these settings:"
        )

    def get_debts_submenu_keyboard(self) -> InlineKeyboard:
        """Generate the debts settings submenu keyboard."""
        return [
            [ButtonCallback("🧾 Debt Correction", "debt_correction")],
            [ButtonCallback("👑 Creditor Royalty", "creditor_royalty")],
            [ButtonCallback(f"{self.ICON_BACK} Back", "back")]
        ]

    def get_debt_correction_submenu_text(self, debt_settings) -> str:
        """Generate debt correction submenu text (method + threshold)."""
        threshold = debt_settings.correction_threshold
        method = debt_settings.correction_method
        method_label = "Absolute" if method == "absolute" else "Proportional"
        return (
            "🧾 **Debt Correction Settings**\n\n"
            "These settings control how the cost of remaining coffees is distributed.\n\n"
            f"**Method:** {method_label}\n"
            f"**Threshold:** {threshold}\n\n"
            "Select a correction setting to adjust:"
        )

    def get_debt_correction_submenu_keyboard(self) -> InlineKeyboard:
        return [
            [ButtonCallback("🧮 Correction Method", "debt_method")],
            [ButtonCallback("🔢 Correction Threshold", "debt_threshold")],
            [ButtonCallback(f"{self.ICON_BACK} Back", "back")]
        ]

    def get_creditor_royalty_submenu_text(self, debt_settings) -> str:
        """Generate the creditor royalty submenu text showing current values."""
        exempt = "✅ Excluded" if debt_settings.creditor_exempt_from_correction else "❌ Included"
        free = int(getattr(debt_settings, "creditor_free_coffees", 0) or 0)
        return (
            "👑 **Creditor Royalty**\n\n"
            "Configure how the creditor (card purchaser) is treated for corrections and\n"
            "optional free coffees the creditor may consume (their cost is borne by others).\n\n"
            f"**Creditor excluded from correction:** {exempt}\n"
            f"**Creditor free coffees:** {free}\n\n"
            "Select an option to change:"
        )

    def get_creditor_royalty_submenu_keyboard(self, debt_settings=None) -> InlineKeyboard:
        exempt = True if debt_settings is None else bool(getattr(debt_settings, "creditor_exempt_from_correction", True))
        tb = toggle_button(exempt, "Exclude creditor from correction", "toggle_creditor_exempt")
        return [
            [ButtonCallback(tb.text, tb.callback_data)],
            [ButtonCallback("🔢 Set free coffees for creditor", "creditor_free_set")],
            [ButtonCallback(f"{self.ICON_BACK} Back", "back")]
        ]

    def get_gsheet_submenu_text(self, gsheet_settings) -> str:
        enabled_status = "✅ On" if gsheet_settings.periodic_sync_enabled else "❌ Off"
        two_way_status = "✅ On" if gsheet_settings.two_way_sync_enabled else "❌ Off"
        after_actions_status = "✅ On" if gsheet_settings.sync_after_actions_enabled else "❌ Off"
        period = int(gsheet_settings.sync_period_minutes)
        return (
            "📄 **Google Sheets Settings (App-Wide)**\n\n"
            f"**Enable periodic syncing:** {enabled_status}\n"
            f"**Syncing period:** {period} min\n"
            f"**Two-way sync:** {two_way_status}\n"
            f"**Sync after actions:** {after_actions_status}\n"
            "Use /sync to trigger a one-shot export.\n\n"
            "Select a setting to adjust:"
        )

    def get_gsheet_submenu_keyboard(self, gsheet_settings) -> InlineKeyboard:
        periodic_enabled = bool(getattr(gsheet_settings, "periodic_sync_enabled", False))
        two_way_enabled = bool(getattr(gsheet_settings, "two_way_sync_enabled", False))
        after_actions_enabled = bool(getattr(gsheet_settings, "sync_after_actions_enabled", False))

        b_periodic = toggle_button(periodic_enabled, "Periodic Sync", "toggle_periodic")
        b_two_way = toggle_button(two_way_enabled, "Two-Way Sync", "toggle_two_way")
        b_after = toggle_button(after_actions_enabled, "Sync After Actions", "toggle_after_actions")

        return [
            [ButtonCallback(b_periodic.text, b_periodic.callback_data)],
            [ButtonCallback("⏱ Set Sync Period (min)", "set_period")],
            [ButtonCallback(b_two_way.text, b_two_way.callback_data)],
            [ButtonCallback(b_after.text, b_after.callback_data)],
            [ButtonCallback(f"{self.ICON_BACK} Back", "back")],
        ]
    
    def get_logging_submenu_text(self, log_settings: Dict) -> str:
        """
        Generate the logging settings submenu text.
        
        Args:
            log_settings: Dictionary with log_level, log_show_time, log_show_caller
            
        Returns:
            Formatted text for the logging submenu
        """
        time_status = "✅ On" if log_settings.get("log_show_time", True) else "❌ Off"
        caller_status = "✅ On" if log_settings.get("log_show_caller", True) else "❌ Off"
        class_status = "✅ On" if log_settings.get("log_show_class", True) else "❌ Off"
        log_level = (log_settings.get("log_level", "INFO") or "INFO").upper()
        log_level_label = format_log_state(log_level) if log_level in LOG_STATE_ICON else log_level
        
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
            "📊 **Logging Settings**\n\n"
            f"**Log Level:** {log_level_label}\n"
            f"**Time Display:** {time_status}\n"
            f"**Caller Display:** {caller_status}\n"
            f"**Class Name Display:** {class_status}\n\n"
            "**Example Preview:**\n"
            f"`{example_log}`\n\n"
            "Select a setting to adjust:"
        )
    
    def get_logging_submenu_keyboard(self) -> InlineKeyboard:
        """Generate the logging settings submenu keyboard."""
        return [
            [ButtonCallback("📊 Logging Level", "log_level")],
            [ButtonCallback("🎨 Logging Format", "log_format")],
            [ButtonCallback("🧩 Module Logging", "log_modules")],
            [ButtonCallback(f"{self.ICON_BACK} Back", "back")]
        ]
    
    def get_notifications_submenu_text(self, notification_settings: Dict, user_settings=None) -> str:
        """
        Generate the notifications settings submenu text.
        
        Args:
            notification_settings: Dictionary with notifications_enabled, notifications_silent from app settings
            user_settings: Unused (kept for call-site compatibility)
            
        Returns:
            Formatted text for the notifications submenu
        """
        app_enabled = bool(notification_settings.get("notifications_enabled", True))
        app_silent = bool(notification_settings.get("notifications_silent", False))

        def _status(on: bool) -> str:
            return "✅ On" if on else "❌ Off"

        silent_label = _status(app_silent) if app_enabled else "⏸ N/A"

        lines = [
            "🔔 **Notifications (Global)**",
            "",
            "**Admin:**",
            f"• **Enabled:** {_status(app_enabled)}",
            f"• **Silent:** {silent_label}",
            "",
        ]

        if not app_enabled:
            lines.append("⚠️ Global notifications are OFF → nobody receives notifications.")
            lines.append("ℹ️ Silent mode is irrelevant while notifications are OFF.")
        elif app_silent:
            lines.append("ℹ️ Global silent is ON → all notifications are silent (overrides user silent).")
        else:
            lines.append("ℹ️ Users can choose silent mode for themselves.")

        lines.append("ℹ️ Users can still disable notifications for themselves.")
        lines.append("\nToggle below:")
        return "\n".join(lines)
    
    def get_notifications_submenu_keyboard(self, notification_settings: Dict) -> InlineKeyboard:
        """Generate the notifications settings submenu keyboard."""
        app_enabled = bool(notification_settings.get("notifications_enabled", True))
        app_silent = bool(notification_settings.get("notifications_silent", False))

        notif_btn = toggle_button(app_enabled, "Notifications", "toggle_notifications")
        # Show silent toggle only when notifications are enabled; otherwise indicate N/A
        if app_enabled:
            silent_btn = toggle_button(app_silent, "Silent", "toggle_silent")
            silent_row = [ButtonCallback(silent_btn.text, silent_btn.callback_data)]
        else:
            silent_row = [ButtonCallback("⏸ Silent: N/A", "toggle_silent")]

        return [
            [ButtonCallback(notif_btn.text, notif_btn.callback_data)],
            silent_row,
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
        time_icon = "✅" if log_settings.get("log_show_time", True) else "❌"
        caller_icon = "✅" if log_settings.get("log_show_caller", True) else "❌"
        class_icon = "✅" if log_settings.get("log_show_class", True) else "❌"
        
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

        bt = toggle_button(time_enabled, "Time", "toggle_time")
        bc = toggle_button(caller_enabled, "Caller", "toggle_caller")
        bj = toggle_button(class_enabled, "Class", "toggle_class")

        return [
            [ButtonCallback(bt.text, bt.callback_data), ButtonCallback(bc.text, bc.callback_data), ButtonCallback(bj.text, bj.callback_data)],
            [ButtonCallback(f"{self.ICON_BACK} Back", "back")]
        ]
    
    def get_log_level_options_text(self, current_level: str) -> str:
        """
        Generate the log level options text.
        
        Args:
            current_level: Current log level
            
        Returns:
            Formatted text for log level options
        """
        current_level = (current_level or "INFO").upper()
        current_level_label = format_log_state(current_level) if current_level in LOG_STATE_ICON else current_level

        return (
            "📊 **Log Level**\n\n"
            "Controls the minimum severity of log messages to display.\n\n"
            f"**Current level:** {current_level_label}\n\n"
            "**Options:**\n"
            f"• **{format_log_state('TRACE')}** - Most verbose, shows all details\n"
            f"• **{format_log_state('DEBUG')}** - Development information\n"
            f"• **{format_log_state('INFO')}** - General information (recommended)\n"
            f"• **{format_log_state('WARNING')}** - Warnings only\n"
            f"• **{format_log_state('ERROR')}** - Errors only\n"
            f"• **{format_log_state('CRITICAL')}** - Critical errors only\n\n"
            "Choose your preferred log level:"
        )
    
    def get_log_level_options_keyboard(self) -> InlineKeyboard:
        """Generate the log level options keyboard."""
        return [
            [ButtonCallback(format_log_state("TRACE"), "TRACE"), ButtonCallback(format_log_state("DEBUG"), "DEBUG")],
            [ButtonCallback(format_log_state("INFO"), "INFO"), ButtonCallback(format_log_state("WARNING"), "WARNING")],
            [ButtonCallback(format_log_state("ERROR"), "ERROR"), ButtonCallback(format_log_state("CRITICAL"), "CRITICAL")],
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
