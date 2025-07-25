"""
Conversation management for Telegram bot.

This module handles multi-step conversation flows including user registration,
authentication, and group selection processes.
"""

import asyncio
from typing import TYPE_CHECKING, Dict, Any, Callable, Optional
from functools import wraps
from pydantic import BaseModel, Field
from telethon import events
from telethon.tl.custom.conversation import Conversation
from pymongo.errors import DuplicateKeyError
from ..handlers import handlers
from ..dependencies import dependencies as dep
from .keyboards import KeyboardManager
from ..common.log import (
    log_telegram_callback, log_conversation_started, log_conversation_step, log_unexpected_error,
    log_conversation_completed, log_conversation_timeout, log_conversation_cancelled
)


class ConversationCancelledException(Exception):
    """Exception raised when a conversation is cancelled by the user."""
    pass


class ConversationState(BaseModel):
    """Represents the current state of a user conversation."""
    user_id: int = Field(..., description="ID of the user in conversation")
    step: str = Field(..., description="Current conversation step")
    data: Dict[str, Any] = Field(default_factory=dict, description="Conversation data storage")
    timeout: int = Field(default=30, gt=0, description="Conversation timeout in seconds")
    conv: Optional[Conversation] = Field(default=None, description="Active Telethon conversation object")
    cancelled: bool = Field(default=False, description="Whether this conversation has been cancelled")
    
    class Config:
        arbitrary_types_allowed = True  # Allow Telethon Conversation object


class ConversationTimeout(BaseModel):
    """Configuration for conversation timeouts."""
    default: int = Field(default=30, gt=0, description="Default timeout in seconds")
    registration: int = Field(default=60, gt=0, description="Registration timeout in seconds") 
    password: int = Field(default=45, gt=0, description="Password input timeout in seconds")
    group_selection: int = Field(default=180, gt=0, description="Group selection timeout in seconds")


if TYPE_CHECKING:
    from ..api.telethon_api import TelethonAPI


def managed_conversation(conversation_type: str, timeout: int = 60):
    """
    Decorator to automatically manage conversation state and Telethon conversation objects.
    
    This decorator:
    - Creates and registers conversation state
    - Creates Telethon conversation object
    - Passes both as parameters to the decorated function
    - Automatically cleans up on completion or error
    - Handles conversation cancellation gracefully
    
    Args:
        conversation_type: Type of conversation (e.g., "registration", "group_selection")
        timeout: Conversation timeout in seconds
        
    Usage:
        @managed_conversation("registration", 60)
        async def register_conversation(self, user_id: int, conv: Conversation, state: ConversationState) -> bool:
            # Your conversation logic here
            pass
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(self, user_id: int, *args, **kwargs) -> Any:
            # Create and register conversation state
            conversation_state = self.create_conversation_state(
                user_id=user_id,
                conversation_type=conversation_type,
                timeout=timeout
            )
            
            try:
                # Create Telethon conversation object
                async with self.api.bot.conversation(user_id) as conv:
                    # Update state with conv object
                    conversation_state.conv = conv
                    
                    print(f"Started managed conversation '{conversation_type}' for user {user_id}")
                    
                    # Call the decorated function with conv and state
                    result = await func(self, user_id, conv, conversation_state, *args, **kwargs)
                    
                    print(f"Completed managed conversation '{conversation_type}' for user {user_id}")
                    return result
            
            except ConversationCancelledException:
                # Handle conversation cancellation via /cancel command
                print(f"Conversation '{conversation_type}' was cancelled by user {user_id}")
                return False
                
            except asyncio.CancelledError:
                # Handle conversation cancellation (e.g., from /cancel command)
                print(f"Conversation '{conversation_type}' was cancelled for user {user_id}")
                # await self.api.message_manager.send_text(
                #     user_id,
                #     "❌ Conversation cancelled.",
                #     True,
                #     True
                # )
                # log_conversation_cancelled(user_id, conversation_type, "conversation_cancelled")
                return False
                
            except Exception as e:
                # Handle other errors
                print(f"Error in managed conversation '{conversation_type}' for user {user_id}: {e}")
                log_unexpected_error(f"managed_conversation_{conversation_type}", str(e), {"user_id": user_id})
                return False
                
            finally:
                # Always clean up conversation state
                self.remove_conversation_state(user_id)
                
        return wrapper
    return decorator


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
        # Manage active conversations within the conversation manager
        self.active_conversations: Dict[int, ConversationState] = {}
    
    # === Conversation Managment === 
    
    def has_active_conversation(self, user_id: int) -> bool:
        """
        Check if there is an active conversation for the given user ID.
        
        Args:
            user_id: Telegram user ID to check
            
        Returns:
            True if an active conversation exists, False otherwise
        """
        return user_id in self.active_conversations
    
    def get_active_conversations(self) -> Dict[int, ConversationState]:
        """
        Get the current active conversations.
        
        Returns:
            Dictionary of active conversations with user_id as key
        """
        return self.active_conversations
    
    def create_conversation_state(self, user_id: int, conversation_type: str, timeout: int = 60) -> ConversationState:
        """
        Create a new conversation state and add it to active conversations.
        
        Args:
            user_id: Telegram user ID
            conversation_type: Type of conversation (e.g., "registration", "group_selection")
            timeout: Conversation timeout in seconds
            
        Returns:
            ConversationState: The created conversation state
        """
        conversation_state = ConversationState(
            user_id=user_id,
            step=f"{conversation_type}_start",
            timeout=timeout
        )
        
        self.active_conversations[user_id] = conversation_state
        print(f"Created conversation state for user {user_id}: {conversation_type}")
        
        return conversation_state
    
    def remove_conversation_state(self, user_id: int) -> bool:
        """
        Remove a conversation state from active conversations.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            bool: True if conversation was removed, False if not found
        """
        if user_id in self.active_conversations:
            del self.active_conversations[user_id]
            print(f"Removed conversation state for user {user_id}")
            return True
        return False
    
    def get_conversation_state(self, user_id: int) -> Optional[ConversationState]:
        """
        Get the conversation state for a user.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            ConversationState or None if not found
        """
        return self.active_conversations.get(user_id)
    
    def update_conversation_step(self, user_id: int, step: str) -> bool:
        """
        Update the conversation step for a user.
        
        Args:
            user_id: Telegram user ID
            step: New conversation step
            
        Returns:
            bool: True if updated successfully, False if conversation not found
        """
        if user_id in self.active_conversations:
            self.active_conversations[user_id].step = step
            print(f"Updated conversation step for user {user_id} to: {step}")
            return True
        return False
    
    def cancel_conversation(self, user_id: int) -> bool:
        """
        Cancel an active conversation for a user.
        
        This method interrupts the conversation by removing it from active conversations
        and optionally cancelling the Telethon conversation object.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            bool: True if conversation was cancelled, False if not found
        """
        if user_id in self.active_conversations:
            conversation_state = self.active_conversations[user_id]
            
            # Set cancelled flag to prevent further processing
            conversation_state.cancelled = True
            
            # Try to cancel the Telethon conversation object if it exists
            if conversation_state.conv:
                try:
                    # Cancel the conversation - this will cause TimeoutError in wait_event calls
                    conversation_state.conv.cancel()
                    print(f"Cancelled Telethon conversation for user {user_id}")
                except Exception as e:
                    print(f"Error cancelling Telethon conversation for user {user_id}: {e}")
            
            # Remove from active conversations
            del self.active_conversations[user_id]
            print(f"Cancelled and removed conversation for user {user_id}")

            log_conversation_cancelled(user_id, conversation_state.step, "user_cancelled")
            
            return True
        
        print(f"No active conversation found for user {user_id} to cancel")
        return False
    
    # === Messages ===

    async def receive_message(self, conv: Conversation, user_id: int, timeout: int = 45):
        """
        Receive a message from a conversation with automatic /cancel detection.
        
        This method wraps conv.wait_event(events.NewMessage) and automatically
        handles /cancel commands by raising ConversationCancelledException.
        
        Args:
            conv: The active Telethon conversation object
            user_id: The user ID for the conversation
            timeout: Timeout in seconds for waiting for the message
            
        Returns:
            The message event if successful
            
        Raises:
            ConversationCancelledException: If the user sends /cancel
            TimeoutError: If no message is received within timeout
        """
        message_event = await conv.wait_event(
            events.NewMessage(incoming=True), 
            timeout=timeout
        )
        
        # Check if the received message is a cancel command
        message_text = message_event.message.message.strip()
        if message_text.lower() == '/cancel':
            await message_event.message.delete()  # Delete the command for security
            self.cancel_conversation(user_id)
            raise ConversationCancelledException("Conversation cancelled by user")
        
        return message_event

    
    
    # ==== Conversations ====
    
    @managed_conversation("registration", 60)
    async def register_conversation(self, user_id: int, conv: Conversation, state: ConversationState) -> bool:
        """
        Start the registration conversation with a user.
        
        This handles the complete user registration flow including:
        - Initial confirmation prompt
        - Password authentication with retry logic
        - Final registration completion
        
        Args:
            user_id: Telegram user ID to register
            conv: Active Telethon conversation object (provided by decorator)
            state: Conversation state object (provided by decorator)
            
        Returns:
            bool: True if registration was successful, False otherwise
        """
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
        log_telegram_callback(user_id, data)
        
        if data == "No":
            await message_register.edit("Register process aborted.", buttons=None)
            log_conversation_cancelled(user_id, "registration", "user_declined")
            return False
        await message_register.edit("Start Register process.", buttons=None)

        # Update conversation state
        self.update_conversation_step(user_id, "password_authentication")

        # Password request
        if not await self.request_authentication(conv):
            return False

        # Get user information from Telegram
        log_conversation_step(user_id, "registration", "fetching_user_info")
        user_entity = await self.api.bot.get_entity(user_id)
        username = getattr(user_entity, 'username', None)
        first_name = getattr(user_entity, 'first_name', None)
        last_name = getattr(user_entity, 'last_name', None)
        phone = getattr(user_entity, 'phone', None)
        photo_id = getattr(user_entity, 'photo', None)
        lang_code = getattr(user_entity, 'lang_code', 'en')
        
        # Debug logging to see what we're getting from Telegram
        print(f"DEBUG: Telegram user entity for user_id {user_id}:")
        print(f"  - username: {username}")
        print(f"  - first_name: {first_name}")
        print(f"  - last_name: {last_name}")
        print(f"  - phone: {phone} (type: {type(phone)})")
        print(f"  - photo_id: {photo_id}")
        print(f"  - lang_code: {lang_code}")
        print(f"  - user_entity type: {type(user_entity)}")
        print(f"  - user_entity attributes: {dir(user_entity)}")
        
        # Extract photo_id if photo exists
        if photo_id and hasattr(photo_id, 'photo_id'):
            photo_id = photo_id.photo_id
        else:
            photo_id = None
        
        if first_name is None: 
            log_conversation_step(user_id, "registration", "first_name_not_found")
            
            await self.api.message_manager.send_text(user_id, 
                "Please provide your first name to complete registration.",
                True,
                True
            )
            first_name_event = await self.receive_message(conv, user_id, 45)
            first_name = first_name_event.message.message.strip().title()
            
        if username is None:
            # combine lowercase first_name and last_name (if available) as username
            username = f"{first_name.lower()}_{last_name.lower()}" if last_name else first_name.lower()
        
        log_conversation_step(user_id, "registration", f"user_info_retrieved: {first_name} (@{username})")
        
        # check if a user with this first_name already exists, and in this case require a last name 
        if last_name is None:
            # existing_user = await dep.get_repo().find_user_by_id(user_id)
            # if existing_user and existing_user.first_name.lower() == first_name.lower():
            log_conversation_step(user_id, "registration", "user_with_first_name_exists")
            await self.api.message_manager.send_text(
                user_id,
                "Please provide you last name.",
                True,
                True
            )
            last_name_event = await self.receive_message(conv, user_id, 45)
            last_name = last_name_event.message.message.strip().title()
            
        # TODO: add a display name (first name and first + last name if user already exists)
        # alternatively put this only in the group keyboard
        
        # Create the user in the database
        try:
            log_conversation_step(user_id, "registration", "creating_user_in_database")
            new_user = await handlers.register_user(
                user_id=user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                phone=phone,
                photo_id=photo_id,
                lang_code=lang_code,
                repo=dep.get_repo()
            )
            
            log_conversation_step(user_id, "registration", "user_created_successfully")
            await self.api.message_manager.send_text(
                user_id,
                f"✅ Registration successful! Welcome {first_name}!",
                True,
                True
            )
            
            log_conversation_completed(user_id, "registration")
            return True
            
        except DuplicateKeyError as e:
            # Handle duplicate key error specifically - usually means phone number already exists
            error_message = str(e)
            if "phone_1 dup key" in error_message:
                log_conversation_step(user_id, "registration", f"duplicate_phone_error: {str(e)}")
                await self.api.message_manager.send_text(
                    user_id,
                    "❌ A user with your phone number is already registered. Please contact your admin.",
                    True,
                    True
                )
            else:
                # Handle other duplicate key errors (e.g., user_id, username)
                log_conversation_step(user_id, "registration", f"duplicate_key_error: {str(e)}")
                await self.api.message_manager.send_text(
                    user_id,
                    "❌ This user account is already registered. Please contact your admin if you believe this is an error.",
                    True,
                    True
                )
            
            log_unexpected_error("user_registration", str(e), {"user_id": user_id})
            return False
            
        except Exception as e:
            log_conversation_step(user_id, "registration", f"user_creation_failed: {str(e)}")
            await self.api.message_manager.send_text(
                user_id,
                "❌ Registration failed. Please try again later.",
                True,
                True
            )
            
            log_unexpected_error("user_registration", str(e), {"user_id": user_id})
            return False
            
    async def request_authentication(self, conv) -> bool:
        """
        Request password authentication from a user in a conversation.
        
        This handles the password authentication flow with:
        - Multiple retry attempts (up to 3)
        - Proper timeout handling
        - Automatic message cleanup for security
        - Progressive error messaging
        
        Args:
            conv: The active Telegram conversation object
            
        Returns:
            bool: True if authentication was successful, False otherwise
        """
        chat = await conv.get_input_chat()
        chat_id = chat.user_id if hasattr(chat, 'user_id') else chat
        
        message_password = await self.api.message_manager.send_text(
            chat_id, 
            "Please enter the password:", 
            True, 
            True
        )
        max_tries = 3
        tries = 0
        
        while tries < max_tries:
            try:
                password_event = await self.receive_message(conv, chat_id, 45)
                
                password = password_event.message.message.strip()
                await password_event.message.delete()  # Delete password for security
                
                authenticated = await handlers.check_password(password, dep.get_repo())
                if authenticated:
                    await self.api.message_manager.send_text(
                        chat_id, 
                        "✅ Password correct!", 
                        True, 
                        True
                    )
                    return True
                else:
                    tries += 1
                    if tries < max_tries:
                        await self.api.message_manager.send_text(
                            chat_id, 
                            f"❌ Password incorrect. Please try again. ({tries}/{max_tries} attempts used)", 
                            True, 
                            True
                        )
                    else:
                        await self.api.message_manager.send_text(
                            chat_id, 
                            "❌ Too many incorrect attempts. Registration aborted.", 
                            True, 
                            True
                        )
                        return False
                    
            except TimeoutError:
                from ..common.log import log_conversation_timeout
                log_conversation_timeout(chat_id, "registration", "password_authentication")
                await self.api.message_manager.send_text(
                    chat_id, 
                    "⏱️ Password entry timed out. Registration aborted.", 
                    True, 
                    True
                )
                return False
                
        return False
    
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
                
                await self.api.message_manager.send_text(user_id, result_message, False)
                log_conversation_completed(user_id, "group_selection")
                
                # TODO: do something with content of self.group
                # This is where you'd call the coffee business logic
                # from ..bot.commands import process_telegram_keyboard_response
                # await process_telegram_keyboard_response(user_id, card_id, self.api.group)
                
            # TODO: reset group to initial state
