"""
Message Management for Telegram Bot

This module handles message operations including sending, editing, deleting,
and managing message lifecycle for the coffee ordering bot.
"""

from typing import Any, Optional, List, Union, TYPE_CHECKING
from pydantic import BaseModel

from .telethon_models import MessageModel
from ..common.log import log_telegram_message_sent, log_telegram_keyboard_sent, log_telegram_api_error, log_telegram_message_deleted, Logger

if TYPE_CHECKING:
    from telethon import TelegramClient, types

class MessageManager:
    """
    Manages message operations for the Telegram bot.
    
    This class handles sending messages, keyboards, and managing message
    lifecycle including cleanup and conversation grouping.
    """

    def __init__(self, bot_client: "TelegramClient"):
        """
        Initialize MessageManager with bot client.
        
        Args:
            bot_client: Telethon TelegramClient instance
        """
        self.bot: "TelegramClient" = bot_client
        self.latest_messages: List[Union["MessageModel", List["MessageModel"], Any]] = []
        self.logger = Logger("MessageManager")

    async def send_message(self, *args: Any, **kwargs: Any) -> 'types.Message':
        """
        Unified low-level message sender used by higher-level helpers.
        
        Accepts all Telethon send_message params.
        """
        message = await self.bot.send_message(*args, **kwargs)
        return message

    async def edit_message(self, message: 'MessageModel', text: str, **kwargs: Any) -> None:
        """
        Edit an existing message.
        
        Note: Reply keyboards cannot be changed via edit.
        """
        edited = await message.edit(text, **kwargs)
        return edited

    async def send_text(
        self, 
        user_id: int, 
        text: str, 
        vanish: bool = True, 
        conv: bool = False,
        silent: bool = True,
        link_preview: bool = False,  # Disable link preview by default
        delete_after: int = 0  # Auto-delete message after N seconds (0 = no auto-delete)
    ) -> Optional["MessageModel"]:
        """
        Send a text message to a user and optionally add it to latest_messages.
        
        Args:
            user_id: Telegram user ID to send message to
            text: Message text content
            vanish: If True, add message to latest_messages for cleanup
            conv: If True, add to conversation group
            silent: If True, send message silently
            link_preview: If True, enable link preview (default: False)
            delete_after: If > 0, automatically delete the message after this many seconds (default: 0)
            
        Returns:
            The sent message object or None if failed
        """
        try:
            telegram_message = await self.send_message(
                user_id,
                text,
                silent=silent,
                link_preview=link_preview
            )
            message_model = MessageModel.from_telegram_message(telegram_message)
            
            if vanish:
                self.add_latest_message(message_model, conv)
            
            # Auto-delete message after delay if requested
            if delete_after > 0:
                async def delete_after_delay():
                    import asyncio
                    await asyncio.sleep(delete_after)
                    try:
                        await message_model.delete()
                    except Exception:
                        pass  # Ignore deletion errors
                
                # Start deletion task in background
                import asyncio
                asyncio.create_task(delete_after_delay())

            log_telegram_message_sent(user_id, "text", text[:50] if text else "")
            return message_model
        except Exception as e:
            log_telegram_api_error("send_message", str(e), user_id)
            return None
    
    async def send_keyboard(
        self,
        user_id: int,
        text: str,
        keyboard_layout: Any,
        vanish: bool = True,
        conv: bool = False,
        silent: bool = True,
        link_preview: bool = False  # Disable link preview by default
    ) -> Optional["MessageModel"]:
        """
        Send a keyboard to the user and optionally add it to latest_messages.
        
        Args:
            user_id: Telegram user ID to send keyboard to
            text: Message text content
            keyboard_layout: 2D list representing keyboard button layout
            vanish: If True, add message to cleanup cache
            conv: If True, add to conversation group
            silent: If True, send message silently
            link_preview: If True, enable link preview (default: False)
            
        Returns:
            The sent message with keyboard or None if failed
        """
        try:
            telegram_message = await self.send_message(
                user_id,
                text,
                buttons=keyboard_layout,
                silent=silent,
                link_preview=link_preview
                # parse_mode='html'
            )
            message_model = MessageModel.from_telegram_message(telegram_message)
            
            if vanish:
                self.add_latest_message(message_model, conv)

            # Count buttons for logging
            button_count = 0
            if keyboard_layout:
                if hasattr(keyboard_layout, '__len__'):
                    button_count = len(keyboard_layout)
                    
            log_telegram_keyboard_sent(user_id, "inline_keyboard", button_count)
            return message_model
        except Exception as e:
            log_telegram_api_error("send_keyboard", str(e), user_id)
            return None
    
    def add_latest_message(
        self, 
        message: Union["MessageModel", Any],
        conv: bool = False, 
        new: bool = False
    ) -> None:
        """
        Add a message to latest_messages, supporting conversation and new flags.
        
        Args:
            message: The message to add (MessageModel or Telegram message object)
            conv: If True, add to existing conversation list
            new: If True, create a new conversation list
        """
        if new:
            self.latest_messages.append([message])
            
        elif conv:
            if len(self.latest_messages) > 0 and isinstance(self.latest_messages[-1], list):
                self.latest_messages[-1].append(message)
            else:
                self.latest_messages.append([message]) 
        else:
            self.latest_messages.append(message)
        
        # Debug logging: Print structure after each addition
        structure = []
        for m in self.latest_messages:
            if isinstance(m, list):
                structure.append(len(m))
            else:
                structure.append(1)
        self.logger.trace(f"Structure after add (new={new}, conv={conv}): {structure}", extra_tag="VANISH DEBUG")
    
    async def delete_oldest_message(self) -> None:
        """
        Delete the oldest message or list of messages from latest_messages.
        
        This method removes and deletes the oldest message(s) from the cache
        to prevent memory buildup and clean up old UI elements.
        """
        if not self.latest_messages:
            return
        
        # Debug logging: Print structure before deletion
        structure_before = []
        for m in self.latest_messages:
            if isinstance(m, list):
                structure_before.append(len(m))
            else:
                structure_before.append(1)
        self.logger.trace(f"Structure before delete: {structure_before}", extra_tag="VANISH DEBUG")
            
        message = self.latest_messages.pop(0)
        if isinstance(message, list):
            import asyncio
            await asyncio.gather(*(m.delete() for m in message))
            self.logger.trace(f"Deleted list with {len(message)} messages", extra_tag="VANISH DEBUG")
        else:
            await message.delete()
            self.logger.trace(f"Deleted single message", extra_tag="VANISH DEBUG")
        
        # Debug logging: Print structure after deletion
        structure_after = []
        for m in self.latest_messages:
            if isinstance(m, list):
                structure_after.append(len(m))
            else:
                structure_after.append(1)
        self.logger.trace(f"Structure after delete: {structure_after}", extra_tag="VANISH DEBUG")
    
    def get_latest_messages_length(self) -> List[Union[int, bool]]:
        """
        Return a list of lengths/types for latest_messages for debugging/UI.
        
        Returns:
            List where each element is either:
            - int: Length of a message list
            - bool: True for single messages
        """
        length = []
        for m in self.latest_messages:
            if isinstance(m, list):
                length.append(len(m))
            else:
                length.append(True)
        return length
    
    async def message_vanisher(self) -> None:
        """Background task to delete old messages after a timeout."""
        import asyncio
        
        while True:
            await asyncio.sleep(10)
            
            if len(self.latest_messages) == 0:
                continue
            
            i = 0
            while i < len(self.latest_messages):
                # delete older messages if list is longer than 3
                if len(self.latest_messages) > 3:
                    await self.delete_oldest_message()
                    continue
                
                # Check for lists in the remaining messages and delete everything before a list 
                if isinstance(self.latest_messages[i], list):
                    for j in range(i):
                        if j >= len(self.latest_messages) - 2:
                            break
                        await self.delete_oldest_message()
        
                i += 1
    
    async def send_notification_to_all_users(
        self,
        text: str,
        silent: bool = False,
        link_preview: bool = False,
        exclude_user_ids: Optional[List[int]] = None,
        exclude_archived: bool = True,
        exclude_disabled: bool = True
    ) -> int:
        """
        Send a notification message to all registered Telegram users.
        
        This is useful for broadcasting important information like:
        - Coffee order summaries
        - Coffee card closures
        - System announcements
        
        Args:
            text: The notification message text
            silent: If True, send message silently without notification sound
            link_preview: If True, enable link preview (default: False)
            exclude_user_ids: Optional list of user IDs to exclude from notification
            exclude_archived: If True, exclude archived users (default: True)
            exclude_disabled: If True, exclude disabled users (default: True)
            
        Returns:
            Number of users who successfully received the notification
        """
        from ..dependencies.dependencies import get_repo
        
        repo = get_repo()
        # Use find_all_telegram_users to only get users with user_id
        telegram_users = await repo.find_all_telegram_users(
            exclude_archived=exclude_archived,
            exclude_disabled=exclude_disabled
        ) or []
        
        exclude_set = set(exclude_user_ids or [])
        sent_count = 0
        
        for user in telegram_users:
            # Skip excluded users
            if user.user_id in exclude_set:
                continue
            
            try:
                await self.send_text(
                    user_id=user.user_id,
                    text=text,
                    vanish=False,  # Don't add to cleanup queue for broadcasts
                    conv=False,
                    silent=silent,
                    link_preview=link_preview
                )
                sent_count += 1
            except Exception as e:
                log_telegram_api_error("send_notification_to_all_users", str(e), user.user_id)
        
        return sent_count
    
    async def send_popup_notification(
        self,
        button_event: Any,
        text: str,
        show_alert: bool = False,
        cache_time: int = 0
    ) -> bool:
        """
        Send a popup notification in response to a callback query (button press).
        
        This shows a brief notification at the top of the chat or an alert popup.
        Perfect for:
        - Confirming setting changes ("✅ Settings updated!")
        - Acknowledging user actions ("✅ Value saved!")
        - Showing errors ("❌ Invalid input")
        
        Args:
            button_event: The Telethon button callback event
            text: The notification text (0-200 characters)
            show_alert: If True, show as alert popup; if False, show as brief notification (default: False)
            cache_time: Maximum time in seconds to cache the result client-side (default: 0)
            
        Returns:
            True if notification was sent successfully, False otherwise
            
        Note:
            - For brief confirmations, use show_alert=False (default)
            - For important alerts/errors, use show_alert=True
            - Text is limited to 200 characters by Telegram
            
        Usage:
            ```python
            # Get button event with return_event=True
            data, message, event = await self.edit_keyboard_and_wait_response(..., return_event=True)
            
            # Send popup notification
            await self.api.message_manager.send_popup_notification(
                event, 
                "✅ Setting saved!", 
                show_alert=False
            )
            ```
        """
        try:
            # Telethon's answer() method on callback queries
            # This is equivalent to answerCallbackQuery in Bot API
            await button_event.answer(
                message=text[:200] if text else None,  # Limit to 200 chars
                alert=show_alert,
                cache_time=cache_time
            )
            return True
        except Exception as e:
            log_telegram_api_error("send_popup_notification", str(e), None)
            return False
