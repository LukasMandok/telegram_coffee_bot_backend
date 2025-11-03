"""
Settings Manager for handling user settings UI and workflows.

This module provides a centralized way to:
- manage user settings conversations and generate consistent menus
- initialize application settings from the database on startup
"""

import asyncio
import logging
from typing import Optional, Tuple, Any, Dict, List
from telethon import Button, events
from telethon.tl.custom import Conversation
from ..dependencies.dependencies import get_repo
from ..common.log import log_settings

logger = logging.getLogger(__name__)


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

                # Update root logger level
                level_map = {
                    'TRACE': 5,
                    'DEBUG': logging.DEBUG,
                    'INFO': logging.INFO,
                    'WARNING': logging.WARNING,
                    'ERROR': logging.ERROR,
                    'CRITICAL': logging.CRITICAL
                }
                logging.root.setLevel(level_map.get(log_settings.level, logging.INFO))

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
    
    def get_main_menu_keyboard(self) -> List[List[Button]]:
        """Generate the main settings menu keyboard."""
        return [
            [Button.inline("📋 Ordering Settings", b"ordering")],
            [Button.inline("💬 Vanishing Messages", b"vanishing")],
            [Button.inline("🔔 Notifications", b"user_notifications")],
            [Button.inline("🔧 Administration", b"admin")],
            [Button.inline("✅ Done", b"done")]
        ]
    
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
    
    def get_ordering_submenu_keyboard(self) -> List[List[Button]]:
        """Generate the ordering settings submenu keyboard."""
        return [
            [Button.inline("📄 Group Page Size", b"page_size")],
            [Button.inline("🔤 Group Sorting", b"sorting")],
            [Button.inline(f"{self.ICON_BACK} Back", b"back")]
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
    
    def get_vanishing_submenu_keyboard(self) -> List[List[Button]]:
        """Generate the vanishing messages submenu keyboard."""
        return [
            [Button.inline("🔄 Toggle On/Off", b"toggle")],
            [Button.inline("🔢 Vanish Threshold", b"threshold")],
            [Button.inline(f"{self.ICON_BACK} Back", b"back")]
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
        app_enabled = notification_settings.get("notifications_enabled", True)
        app_silent = notification_settings.get("notifications_silent", False)
        user_silent = user_settings.notifications_silent
        
        user_silent_status = "✅ On" if user_silent else "❌ Off"
        
        text = (
            "🔔 **My Notification Preferences**\n\n"
            f"**Silent Mode:** {user_silent_status}\n\n"
        )
        
        if not app_enabled:
            text += (
                "⚠️ **Notifications are currently disabled globally by an admin.**\n"
                "You won't receive any notifications regardless of your preference.\n\n"
            )
        elif app_silent:
            text += (
                "ℹ️ **Global silent mode is ON (set by admin).**\n"
                "All notifications are sent silently regardless of your preference.\n"
                "Your setting will take effect if the admin changes this.\n\n"
            )
        else:
            text += (
                "✅ **Your preference is active!**\n"
                "• **Silent Mode OFF:** You'll receive notifications with sound\n"
                "• **Silent Mode ON:** You'll receive notifications silently\n\n"
            )
        
        text += "Toggle your preference below:"
        
        return text
    
    def get_user_notifications_submenu_keyboard(self) -> List[List[Button]]:
        """Generate the user notification preference submenu keyboard."""
        return [
            [Button.inline("🔇 Toggle Silent Mode", b"toggle_user_silent")],
            [Button.inline(f"{self.ICON_BACK} Back", b"back")]
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
    
    def get_sorting_options_keyboard(self) -> List[List[Button]]:
        """Generate the sorting options keyboard."""
        return [
            [Button.inline("🔤 Alphabetical", b"alphabetical")],
            [Button.inline("☕ Coffee Count", b"coffee_count")],
            [Button.inline(f"{self.ICON_BACK} Back", b"back")]
        ]
    
    def get_cancel_keyboard(self) -> List[List[Button]]:
        """Generate a simple back button keyboard."""
        return [
            [Button.inline(f"{self.ICON_BACK} Back", b"back")]
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
    
    def get_admin_submenu_keyboard(self) -> List[List[Button]]:
        """Generate the admin settings submenu keyboard."""
        return [
            [Button.inline("📊 Logging", b"logging")],
            [Button.inline("🔔 Notifications", b"notifications")],
            [Button.inline(f"{self.ICON_BACK} Back", b"back")]
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
        log_level = log_settings.get("log_level", "INFO")
        
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
            f"**Log Level:** {log_level}\n"
            f"**Time Display:** {time_status}\n"
            f"**Caller Display:** {caller_status}\n"
            f"**Class Name Display:** {class_status}\n\n"
            "**Example Preview:**\n"
            f"`{example_log}`\n\n"
            "Select a setting to adjust:"
        )
    
    def get_logging_submenu_keyboard(self) -> List[List[Button]]:
        """Generate the logging settings submenu keyboard."""
        return [
            [Button.inline("📊 Logging Level", b"log_level")],
            [Button.inline("🎨 Logging Format", b"log_format")],
            [Button.inline(f"{self.ICON_BACK} Back", b"back")]
        ]
    
    def get_notifications_submenu_text(self, notification_settings: Dict, user_settings=None) -> str:
        """
        Generate the notifications settings submenu text.
        
        Args:
            notification_settings: Dictionary with notifications_enabled, notifications_silent from app settings
            user_settings: Optional UserSettings object for user preference display
            
        Returns:
            Formatted text for the notifications submenu
        """
        app_enabled = notification_settings.get("notifications_enabled", True)
        app_silent = notification_settings.get("notifications_silent", False)
        
        app_enabled_status = "✅ On" if app_enabled else "❌ Off"
        app_silent_status = "✅ On" if app_silent else "❌ Off"
        
        text = (
            "🔔 **Notification Settings (App-Wide)**\n\n"
            f"**Notifications Enabled:** {app_enabled_status}\n"
        )
        
        if app_enabled:
            text += (
                f"**Silent Mode (Global):** {app_silent_status}\n\n"
                "**⚙️ How it works:**\n"
                "• If enabled, all notifications are sent to users\n"
                "• If silent mode is ON, all users receive silent notifications (overrides user preference)\n"
                "• If silent mode is OFF, users can choose their own preference\n\n"
            )
        else:
            text += "\n⚠️ **Notifications are disabled globally. No users will receive notifications.**\n\n"
        
        # Show user preference section if provided
        if user_settings:
            user_silent = user_settings.notifications_silent
            user_silent_status = "✅ On" if user_silent else "❌ Off"
            
            text += (
                f"**─────────────────**\n"
                f"**Your Personal Preference:** {user_silent_status}\n"
            )
            
            if app_silent:
                text += "ℹ️ *Currently has no effect because global silent mode is ON*\n\n"
            elif not app_enabled:
                text += "ℹ️ *Currently has no effect because notifications are disabled*\n\n"
            else:
                text += "✅ *Your preference is active*\n\n"
        
        text += "Select a setting to adjust:"
        
        return text
    
    def get_notifications_submenu_keyboard(self, notification_settings: Dict) -> List[List[Button]]:
        """Generate the notifications settings submenu keyboard."""
        app_enabled = notification_settings.get("notifications_enabled", True)
        
        buttons = [
            [Button.inline("🔄 Toggle Notifications", b"toggle_notifications")]
        ]
        
        # Only show silent mode toggle if notifications are enabled
        if app_enabled:
            buttons.append([Button.inline("🔇 Toggle Silent Mode (Global)", b"toggle_silent")])
        
        buttons.append([Button.inline(f"{self.ICON_BACK} Back", b"back")])
        return buttons
    
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
    
    def get_logging_format_keyboard(self, log_settings: Dict) -> List[List[Button]]:
        """
        Generate the logging format keyboard with toggle buttons.
        
        Args:
            log_settings: Dictionary with log_show_time, log_show_caller, log_show_class
            
        Returns:
            Keyboard with toggle buttons
        """
        time_icon = "✅" if log_settings.get("log_show_time", True) else "❌"
        caller_icon = "✅" if log_settings.get("log_show_caller", True) else "❌"
        class_icon = "✅" if log_settings.get("log_show_class", True) else "❌"
        
        return [
            [
                Button.inline(f"{time_icon} Time", b"toggle_time"),
                Button.inline(f"{caller_icon} Caller", b"toggle_caller"),
                Button.inline(f"{class_icon} Class", b"toggle_class")
            ],
            [Button.inline(f"{self.ICON_BACK} Back", b"back")]
        ]
    
    def get_log_level_options_text(self, current_level: str) -> str:
        """
        Generate the log level options text.
        
        Args:
            current_level: Current log level
            
        Returns:
            Formatted text for log level options
        """
        return (
            "📊 **Log Level**\n\n"
            "Controls the minimum severity of log messages to display.\n\n"
            f"**Current level:** {current_level}\n\n"
            "**Options:**\n"
            "• **TRACE** - Most verbose, shows all details\n"
            "• **DEBUG** - Development information\n"
            "• **INFO** - General information (recommended)\n"
            "• **WARNING** - Warnings only\n"
            "• **ERROR** - Errors only\n"
            "• **CRITICAL** - Critical errors only\n\n"
            "Choose your preferred log level:"
        )
    
    def get_log_level_options_keyboard(self) -> List[List[Button]]:
        """Generate the log level options keyboard."""
        return [
            [Button.inline("TRACE", b"TRACE"), Button.inline("DEBUG", b"DEBUG")],
            [Button.inline("INFO", b"INFO"), Button.inline("WARNING", b"WARNING")],
            [Button.inline("ERROR", b"ERROR"), Button.inline("CRITICAL", b"CRITICAL")],
            [Button.inline(f"{self.ICON_BACK} Back", b"back")]
        ]

    
    # === Input Handlers ===
    
    async def get_number_input(
        self,
        conv: Conversation,
        user_id: int,
        message_to_edit: Any,
        setting_name: str,
        description: str,
        current_value: int,
        min_value: int,
        max_value: int,
        max_attempts: int = 3
    ) -> Optional[int]:
        """
        Generic handler for getting number input from user with validation.
        
        This handles the common pattern of:
        1. Showing description and current value
        2. Waiting for number input or cancel
        3. Validating input range
        4. Retrying on invalid input
        
        Args:
            conv: Active conversation
            user_id: User ID
            message_to_edit: Message to edit with prompt
            setting_name: Display name of the setting (e.g., "Group Page Size")
            description: Description of what the setting does
            current_value: Current value of the setting
            min_value: Minimum allowed value (inclusive)
            max_value: Maximum allowed value (inclusive)
            max_attempts: Maximum number of retry attempts
            
        Returns:
            The validated number if successful, None if cancelled or failed
        """
        # Show description and prompt
        prompt_text = (
            f"🔢 **{setting_name}**\n\n"
            f"{description}\n\n"
            f"**Current value:** {current_value}\n"
            f"**Allowed range:** {min_value}-{max_value}"
        )
        
        await self.api.message_manager.edit_message(
            message_to_edit,
            prompt_text,
            buttons=self.get_cancel_keyboard()
        )
        
        attempts = 0
        
        while attempts < max_attempts:
            try:
                # Wait for either text or button
                done, pending = await asyncio.wait(
                    [
                        asyncio.create_task(conv.wait_event(
                            events.NewMessage(incoming=True, from_users=user_id), 
                            timeout=60
                        )),
                        asyncio.create_task(conv.wait_event(
                            self.api.keyboard_callback(user_id), 
                            timeout=60
                        ))
                    ],
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # Cancel pending tasks
                for task in pending:
                    task.cancel()
                
                result = done.pop().result()
                
                # Check if it's a button press (back)
                if hasattr(result, 'data'):
                    button_data = result.data.decode('utf-8')
                    if button_data == "back":
                        await result.answer()
                        return None
                else:
                    # It's a text message
                    user_input = result.message.message.strip()
                    user_message = result.message
                    
                    try:
                        value = int(user_input)
                        
                        if value < min_value or value > max_value:
                            raise ValueError("Value out of range")
                        
                        # Success! Delete the user's input message to keep chat clean
                        try:
                            await user_message.delete()
                        except Exception:
                            pass  # Ignore if we can't delete
                        
                        # Send a temporary success message that will auto-delete after 2 seconds
                        # Don't add to vanish queue since it auto-deletes itself
                        await self.api.message_manager.send_text(
                            user_id,
                            f"✅ **{setting_name} updated to {value}!**",
                            vanish=False,  # Don't add to vanish queue - it auto-deletes
                            conv=False,    # Not part of conversation group
                            delete_after=2
                        )
                        
                        return value
                        
                    except ValueError:
                        attempts += 1
                        remaining = max_attempts - attempts
                        
                        # Delete invalid input message
                        try:
                            await user_message.delete()
                        except Exception:
                            pass
                        
                        if remaining > 0:
                            await self.api.message_manager.send_text(
                                user_id,
                                f"❌ Invalid input. Please enter a number between {min_value} and {max_value}.\n"
                                f"Attempts remaining: {remaining}",
                                True, True
                            )
                        else:
                            await self.api.message_manager.send_text(
                                user_id,
                                "❌ Too many invalid attempts. Settings unchanged.",
                                True, True
                            )
                            return None
                            
            except asyncio.TimeoutError:
                await self.api.message_manager.send_text(
                    user_id,
                    "⏱️ Response timeout. Settings unchanged.",
                    True, True
                )
                return None
        
        return None
    
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
