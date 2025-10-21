"""
Settings Manager for handling user settings UI and workflows.

This module provides a centralized way to manage user settings conversations,
generate consistent menus, and handle common input patterns.
"""

import asyncio
import logging
from typing import Optional, Tuple, Any, Dict, List
from telethon import Button, events
from telethon.tl.custom import Conversation

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
            [Button.inline(f"{self.ICON_CANCEL} Cancel", b"cancel")]
        ]
    
    def get_cancel_keyboard(self) -> List[List[Button]]:
        """Generate a simple cancel button keyboard."""
        return [
            [Button.inline(f"{self.ICON_CANCEL} Cancel", b"cancel")]
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
            f"**Allowed range:** {min_value}-{max_value}\n\n"
            f"Please enter a number between {min_value} and {max_value}, or press Cancel:"
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
                
                # Check if it's a button press (cancel)
                if hasattr(result, 'data'):
                    button_data = result.data.decode('utf-8')
                    if button_data == "cancel":
                        await result.answer()
                        return None
                else:
                    # It's a text message
                    user_input = result.message.message.strip()
                    
                    try:
                        value = int(user_input)
                        
                        if value < min_value or value > max_value:
                            raise ValueError("Value out of range")
                        
                        # Success!
                        return value
                        
                    except ValueError:
                        attempts += 1
                        remaining = max_attempts - attempts
                        
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
