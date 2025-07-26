"""
Message Management for Telegram Bot

This module handles message operations including sending, editing, deleting,
and managing message lifecycle for the coffee ordering bot.
"""

from typing import Any, Optional, List, Union, TYPE_CHECKING
from pydantic import BaseModel

from .telethon_models import MessageModel
from ..common.log import log_telegram_message_sent, log_telegram_keyboard_sent, log_telegram_api_error, log_telegram_message_deleted


class MessageManager:
    """
    Manages message operations for the Telegram bot.
    
    This class handles sending messages, keyboards, and managing message
    lifecycle including cleanup and conversation grouping.
    """
    
    def __init__(self, bot_client: Any):
        """
        Initialize MessageManager with bot client.
        
        Args:
            bot_client: Telethon TelegramClient instance
        """
        self.bot = bot_client
        self.latest_messages: List[Union["MessageModel", List["MessageModel"], Any]] = []
    
    async def send_text(
        self, 
        user_id: int, 
        text: str, 
        vanish: bool = True, 
        conv: bool = False
    ) -> Optional["MessageModel"]:
        """
        Send a text message to a user and optionally add it to latest_messages.
        
        Args:
            user_id: Telegram user ID to send message to
            text: Message text content
            vanish: If True, add message to latest_messages for cleanup
            conv: If True, add to conversation group
            
        Returns:
            The sent message object or None if failed
        """
        try:
            telegram_message = await self.bot.send_message(user_id, text)
            message_model = MessageModel.from_telegram_message(telegram_message)
            
            if vanish:
                self.add_latest_message(message_model, conv)

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
        conv: bool = False
    ) -> Optional["MessageModel"]:
        """
        Send a keyboard to the user and optionally add it to latest_messages.
        
        Args:
            user_id: Telegram user ID to send keyboard to
            text: Message text content
            keyboard_layout: 2D list representing keyboard button layout
            vanish: If True, add message to cleanup cache
            conv: If True, add to conversation group
            
        Returns:
            The sent message with keyboard or None if failed
        """
        try:
            telegram_message = await self.bot.send_message(
                user_id,
                text,
                buttons=keyboard_layout,
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
    
    async def delete_oldest_message(self) -> None:
        """
        Delete the oldest message or list of messages from latest_messages.
        
        This method removes and deletes the oldest message(s) from the cache
        to prevent memory buildup and clean up old UI elements.
        """
        if not self.latest_messages:
            return
            
        message = self.latest_messages.pop(0)
        if isinstance(message, list):
            import asyncio
            await asyncio.gather(*(m.delete() for m in message))
        else:
            await message.delete()
    
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
