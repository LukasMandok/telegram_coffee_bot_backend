"""
Conversation management for Telegram bot.

This module handles multi-step conversation flows including user registration,
authentication, and group selection processes.
"""

from typing import TYPE_CHECKING, Dict, Any
from pydantic import BaseModel, Field
from telethon import events
from ..handlers import handlers
from ..dependencies import dependencies as dep
from .keyboards import KeyboardManager


class ConversationState(BaseModel):
    """Represents the current state of a user conversation."""
    user_id: int = Field(..., description="ID of the user in conversation")
    step: str = Field(..., description="Current conversation step")
    data: Dict[str, Any] = Field(default_factory=dict, description="Conversation data storage")
    timeout: int = Field(default=30, gt=0, description="Conversation timeout in seconds")


class ConversationTimeout(BaseModel):
    """Configuration for conversation timeouts."""
    default: int = Field(default=30, gt=0, description="Default timeout in seconds")
    registration: int = Field(default=60, gt=0, description="Registration timeout in seconds") 
    password: int = Field(default=45, gt=0, description="Password input timeout in seconds")
    group_selection: int = Field(default=180, gt=0, description="Group selection timeout in seconds")


if TYPE_CHECKING:
    from ..api.telethon_api import TelethonAPI


class ConversationManager:
    """
    Manages multi-step conversations for the Telegram bot.
    
    This class handles complex conversation flows that require multiple
    user interactions, timeouts, and state management.
    """
    
    def __init__(self, api: "TelethonAPI"):
        """
        Initialize the conversation manager.
        
        Args:
            api: The TelethonAPI instance for bot communication
        """
        self.api = api
    
    async def register_conversation(self, user_id: int) -> bool:
        """
        Start the registration conversation with a user.
        
        This handles the complete user registration flow including:
        - Initial confirmation prompt
        - Password authentication with retry logic
        - Final registration completion
        
        Args:
            user_id: Telegram user ID to register
            
        Returns:
            bool: True if registration was successful, False otherwise
        """
        async with self.api.bot.conversation(user_id) as conv:
            chat = await conv.get_input_chat()

            # Start registration process
            message_register = await self.api.message_manager.send_keyboard(
                user_id, 
                "Do you want to register?", 
                KeyboardManager.get_confirmation_keyboard(), 
                True, 
                True
            )
            if message_register is None:
                return False
                
            button_event: events.CallbackQuery.Event = await conv.wait_event(
                KeyboardManager.get_keyboard_callback_filter(user_id), 
                timeout=30
            )
            data = button_event.data.decode('utf8')
            if data == "No":
                await message_register.edit("Register process aborted.", buttons=None)
                return False
            await message_register.edit("Start Register process.", buttons=None)

            # Password request
            if not await self.request_authentication(conv):
                return False

            return True
            
    async def request_authentication(self, conv) -> bool:
        """
        Request password authentication from a user in a conversation.
        
        This handles the password authentication flow with:
        - Multiple retry attempts (up to 3)
        - Automatic message cleanup for security
        - Progressive error messaging
        
        Args:
            conv: The active Telegram conversation object
            
        Returns:
            bool: True if authentication was successful, False otherwise
        """
        chat = await conv.get_input_chat()
        
        message_password = await self.api.message_manager.send_text(
            chat.user_id if hasattr(chat, 'user_id') else chat, 
            "Please enter the password:", 
            True, 
            True
        )
        max_tries = 3
        tries = 0
        authenticated = False
        
        while tries < max_tries:
            password_event = await conv.wait_event(
                events.NewMessage(incoming=True), 
                timeout=30
            )
            password = password_event.message.message
            await password_event.message.delete()  # Delete password for security
            
            authenticated = await handlers.check_password(password, dep.get_repo())
            if authenticated:
                return True
            else:
                chat_id = chat.user_id if hasattr(chat, 'user_id') else chat
                await self.api.message_manager.send_text(
                    chat_id, 
                    "Password incorrect. Please try again.", 
                    True, 
                    True
                )
                tries += 1
                
        if not authenticated:
            chat_id = chat.user_id if hasattr(chat, 'user_id') else chat
            await self.api.message_manager.send_text(
                chat_id, 
                "Too many tries. Aborting registration.", 
                True, 
                True
            )
            return False
        
        return False  # Should not reach here, but ensures all paths return
    
    async def group_selection(self, user_id: int) -> None:
        """
        Start the group selection conversation with a user.
        
        This handles the interactive coffee ordering interface with:
        - Dynamic keyboard updates based on user selections
        - Pagination for large member lists
        - Real-time coffee count updates
        - Submit/cancel functionality
        
        Args:
            user_id: Telegram user ID for the conversation
        """
        async with self.api.bot.conversation(user_id) as conv:
            chat = await conv.get_input_chat()
                        
            message = await self.api.message_manager.send_keyboard(
                user_id, 
                "Group View", 
                KeyboardManager.get_group_keyboard(self.api.group_state), 
                True, 
                True
            )
            if message is None:
                return
                
            submitted = False
            current_keyboard = None
            
            while True:          
                button_event = await conv.wait_event(
                    KeyboardManager.get_keyboard_callback_filter(user_id), 
                    timeout=180
                )
                button_data = button_event.data.decode('utf8')

                await button_event.answer()
                                            
                if button_data == "group_submit":
                    await message.edit("Submitted", buttons=None)
                    submitted = True
                    break
                elif button_data == "group_cancel":
                    # TODO: reset the group to initial state and set current_page to 0
                    await message.edit("Canceled", buttons=None)
                    break
                
                elif "group_plus" in button_data:
                    name = button_data.split("_")[2]
                    self.api.group[name] += 1
                elif "group_minus" in button_data:
                    name = button_data.split("_")[2]
                    self.api.group[name] -= 1 if self.api.group[name] > 0 else 0
                    
                elif "group_next" in button_data:
                    # TODO: replace this by the actual number of maximal pages
                    self.api.current_page = min(self.api.current_page + 1, 2)
                elif "group_prev" in button_data:
                    self.api.current_page = max(self.api.current_page - 1, 0)
                    
                group_keyboard = KeyboardManager.get_group_keyboard(self.api.group_state)
                if current_keyboard != group_keyboard:
                    current_keyboard = group_keyboard
                    await message.edit("Group View", buttons=group_keyboard)
                
            if submitted:
                total = sum(self.api.group.values())
                result_message = f"added **{total}** coffees:\n"
                for name, value in self.api.group.items():
                    if value != 0:
                        result_message += f"\t{name}: {value}\n"
                print(result_message)
                await self.api.message_manager.send_text(user_id, result_message, False)
                
                # TODO: do something with content of self.group
                # This is where you'd call the coffee business logic
                # from ..bot.commands import process_telegram_keyboard_response
                # await process_telegram_keyboard_response(user_id, card_id, self.api.group)
                
            # TODO: reset group to initial state
