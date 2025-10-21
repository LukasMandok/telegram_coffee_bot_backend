"""
Conversation management for Telegram bot.

This module handles multi-step conversation flows including user registration,
authentication, and group selection processes.
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Dict, Any, Callable, Optional, cast
from functools import wraps
from pydantic import BaseModel, Field, ValidationError
import pydantic_core
from telethon import events, Button
from telethon.tl.custom.conversation import Conversation
from pymongo.errors import DuplicateKeyError

from ..handlers import handlers
from ..handlers.paypal import create_paypal_link, validate_paypal_link
from ..dependencies import dependencies as dep
from .settings import SettingsManager
from .keyboards import KeyboardManager

logger = logging.getLogger(__name__)
            
from ..common.log import (
    log_telegram_callback, log_conversation_started, log_conversation_step, log_unexpected_error,
    log_conversation_completed, log_conversation_timeout, log_conversation_cancelled
)

if TYPE_CHECKING:
    from ..api.telethon_api import TelethonAPI



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


def managed_conversation(conversation_type: str, timeout: int = 60, use_existing_conv: bool = False):
    """
    Decorator to automatically manage conversation state and Telethon conversation objects.
    
    This decorator:
    - Creates and registers conversation state
    - Creates Telethon conversation object (or reuses existing one for sub-conversations)
    - Passes both as parameters to the decorated function
    - Automatically cleans up on completion or error
    - Handles conversation cancellation gracefully
    
    Args:
        conversation_type: Type of conversation (e.g., "registration", "group_selection")
        timeout: Conversation timeout in seconds
        use_existing_conv: If True, expects 'existing_conv' to be passed in kwargs for sub-conversations
        
    Usage:
        @managed_conversation("registration", 60)
        async def register_conversation(self, user_id: int, conv: Conversation, state: ConversationState) -> bool:
            # Your conversation logic here
            pass
            
        # For sub-conversations:
        @managed_conversation("setup_paypal", 120, use_existing_conv=True)
        async def setup_paypal(self, user_id: int, conv: Conversation, state: ConversationState, existing_conv: Conversation) -> bool:
            # Will reuse the existing conversation from parent
            pass
    """
    def decorator(func: Callable) -> Callable:
        async def wrapper(self, user_id: int, *args, **kwargs):
            # Extract existing conversation if provided
            existing_conv = kwargs.pop('existing_conv', None) if use_existing_conv else None
            
            # Create and register conversation state
            state = self.create_conversation_state(user_id, conversation_type, timeout)
            
            timed_out = False
            try:
                if use_existing_conv and existing_conv is not None:
                    # Use existing conversation (for sub-conversations)
                    state.conv = existing_conv
                    try:
                        result = await func(self, user_id, existing_conv, state, *args, **kwargs)
                        return result
                    except ConversationCancelledException:
                        log_conversation_cancelled(user_id, conversation_type, reason="user_cancelled")
                        return False
                else:
                    # Open a new Telethon conversation and pass it into the wrapped function
                    async with self.api.bot.conversation(user_id) as conv:
                        state.conv = conv
                        try:
                            result = await func(self, user_id, conv, state, *args, **kwargs)
                            return result
                        except ConversationCancelledException:
                            log_conversation_cancelled(user_id, conversation_type, reason="user_cancelled")
                            return False
            except asyncio.TimeoutError:
                # Mark that a timeout occurred
                timed_out = True
                raise
            except Exception as e:
                # Log unexpected errors and return False to the caller
                log_unexpected_error(f"managed_conversation_{conversation_type}", str(e), {"user_id": user_id})
                return False
            finally:
                # Only clean up if not timed out (let exception handler handle timeouts)
                if not timed_out:
                    try:
                        self.remove_conversation_state(user_id)
                    except Exception:
                        pass

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
        # Initialize settings manager for handling settings UI
        self.settings_manager = SettingsManager(api)
    
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
    
    def has_conversation(self, user_id: int) -> bool:
        """
        Check if a user has an active conversation.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            True if user has an active conversation, False otherwise
        """
        return user_id in self.active_conversations
    
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
            
            # Remove from active conversations (this will also show the persistent keyboard)
            self.remove_conversation_state(user_id)

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

    async def receive_button_response(self, conv: Conversation, user_id: int, timeout: int = 60, return_event: bool = False):
        """
        Receive a button callback response from a conversation.
        
        This method wraps conv.wait_event for button callbacks and automatically
        handles the response, logging, and data extraction.
        
        Args:
            conv: The active Telethon conversation object
            user_id: The user ID for the conversation
            timeout: Timeout in seconds for waiting for the button response
            return_event: If True, return tuple of (data, event) instead of just data
            
        Returns:
            str: The button data if successful, None if failed
            tuple: (button_data, button_event) if return_event=True
            
        Raises:
            TimeoutError: If no button response is received within timeout
        """
        try:
            button_event = await conv.wait_event(
                KeyboardManager.get_keyboard_callback_filter(user_id),
                timeout=timeout
            )
            
            data = button_event.data.decode('utf8')
            log_telegram_callback(user_id, data)
            
            if return_event:
                # Don't answer yet - let caller send custom popup notification
                return data, button_event
            else:
                # Answer without notification by default for backward compatibility
                await button_event.answer()
                return data
        except Exception as e:
            # Let the caller handle the exception
            raise e

    async def send_keyboard_and_wait_response(self, conv: Conversation, user_id: int, message_text: str, keyboard, timeout: int = 60) -> tuple[Optional[str], Optional[Any]]:
        """
        Send a keyboard message and wait for a button response in one operation.
        
        This method combines sending a keyboard message and waiting for the button response,
        which is a very common pattern in conversation flows.
        
        Args:
            conv: The active Telethon conversation object
            user_id: The user ID for the conversation
            message_text: The text message to send with the keyboard
            keyboard: The keyboard to send
            timeout: Timeout in seconds for waiting for the button response
            
        Returns:
            tuple: (button_data, message_object) - Both the button response data and the original message object
                   (None, None) if sending the message failed
                   (None, message_object) if button response failed but message was sent
        """
        # Send the keyboard message
        message = await self.api.message_manager.send_keyboard(
            user_id,
            message_text,
            keyboard,
            True,
            True
        )
        
        if message is None:
            return None, None
            
        try:
            # Wait for button response
            data = await self.receive_button_response(conv, user_id, timeout)
            return data, message
        except Exception as e:
            # Return the message object even if button response failed
            # so caller can still use it for error handling
            return None, message

    async def edit_keyboard_and_wait_response(self, conv: Conversation, user_id: int, message_text: str, keyboard, message_to_edit: Any, timeout: int = 60, return_event: bool = False):
        """
        Edit an existing message with a new keyboard and wait for button response.
        
        This method is used for navigating between menus without sending new messages.
        
        Args:
            conv: The active Telethon conversation object
            user_id: The user ID for the conversation
            message_text: The new text for the message
            keyboard: The new keyboard to display
            message_to_edit: The existing message to edit
            timeout: Timeout in seconds for waiting for the button response
            return_event: If True, return tuple of (data, message, event) instead of (data, message)
            
        Returns:
            tuple: (button_data, message_object) - Both the button response data and the message object
                   (button_data, message_object, button_event) if return_event=True
                   (None, message_object) if button response failed but message was edited
        """
        try:
            # Edit the message with new text and keyboard
            await self.api.message_manager.edit_message(message_to_edit, message_text, buttons=keyboard)
            
            # Wait for button response
            if return_event:
                data, event = await self.receive_button_response(conv, user_id, timeout, return_event=True)
                return data, message_to_edit, event
            else:
                data = await self.receive_button_response(conv, user_id, timeout)
                return data, message_to_edit
        except Exception as e:
            # Return the message object even if button response failed
            print(f"Error in edit_keyboard_and_wait_response: {e}")
            if return_event:
                return None, message_to_edit, None
            return None, message_to_edit

    async def send_or_edit_message(self, user_id: int, text: str, message_to_edit: Optional[Any] = None, remove_buttons: bool = False):
        """
        Edit an existing message or send a new text message if editing is not possible.
        
        This is a common pattern where we want to update an existing message if we have it,
        or send a new message if we don't.
        
        Args:
            user_id: The user ID to send the message to
            text: The text content for the message
            message_to_edit: The existing message object to edit (if available)
            remove_buttons: Whether to remove buttons when editing (set buttons=None)
        """
        if message_to_edit:
            try:
                if remove_buttons:
                    await self.api.message_manager.edit_message(message_to_edit, text, buttons=None)
                else:
                    await self.api.message_manager.edit_message(message_to_edit, text)
            except Exception as e:
                # If editing fails, fall back to sending new message
                print(f"Failed to edit message, sending new one: {e}")
                await self.api.message_manager.send_text(user_id, text, True, True)
        else:
            await self.api.message_manager.send_text(user_id, text, True, True)

    async def send_text_and_wait_message(self, conv: Conversation, user_id: int, text: str, timeout: int = 45):
        """
        Send a text message and wait for a text message response in one operation.
        
        This method combines sending a text message and waiting for the user's text response,
        which is a common pattern in conversation flows for collecting user input.
        
        Args:
            conv: The active Telethon conversation object
            user_id: The user ID for the conversation
            text: The text message to send
            timeout: Timeout in seconds for waiting for the message response
            
        Returns:
            The message event if successful
            
        Raises:
            ConversationCancelledException: If the user sends /cancel
            TimeoutError: If no message is received within timeout
        """
        await self.api.message_manager.send_text(user_id, text, True, True)
        return await self.receive_message(conv, user_id, timeout)

    
    
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
        data, message_register = await self.send_keyboard_and_wait_response(
            conv, 
            user_id, 
            "Do you want to register?", 
            KeyboardManager.get_confirmation_keyboard(), 
            30
        )
        if data is None:
            return False
        
        if data == "No":
            await self.send_or_edit_message(user_id, "Register process aborted.", message_register, remove_buttons=True)
            log_conversation_cancelled(user_id, "registration", "user_declined")
            return False
            
        await self.send_or_edit_message(user_id, "Start Register process.", message_register, remove_buttons=True)

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
        
        # Normalize phone: convert empty strings or whitespace to None
        if phone is not None and (not isinstance(phone, str) or not phone.strip()):
            phone = None
        
        # Debug logging to see what we're getting from Telegram
        print(f"DEBUG: Telegram user entity for user_id {user_id}:")
        print(f"  - username: {username}")
        print(f"  - first_name: {first_name}")
        print(f"  - last_name: {last_name}")
        print(f"  - phone: {phone} (type: {type(phone)})")
        print(f"  - photo_id: {photo_id}")
        print(f"  - lang_code: {lang_code}")
        print(f"  - user_entity type: {type(user_entity)}")
        
        # Extract photo_id if photo exists
        if photo_id and hasattr(photo_id, 'photo_id'):
            photo_id = photo_id.photo_id
        else:
            photo_id = None
        
        if first_name is None: 
            log_conversation_step(user_id, "registration", "first_name_not_found")
            
            first_name_event = await self.send_text_and_wait_message(
                conv, user_id, 
                "Please provide your first name to complete registration.",
                45
            )
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
            last_name_event = await self.send_text_and_wait_message(
                conv, user_id,
                "Please provide you last name.",
                45
            )
            last_name = last_name_event.message.message.strip().title()
            
        
        # Check if a passive user with the same name already exists
        # TODO: maybe a search only for the last name 
        log_conversation_step(user_id, "registration", "checking_for_existing_passive_user")
        print(f"DEBUG: Checking for passive user with name: '{first_name}' '{last_name}'")
        existing_passive_user = await handlers.find_passive_user_by_name(
            first_name=first_name,
            last_name=last_name
        )
        print(f"DEBUG: Found existing passive user: {existing_passive_user}")
        
        if existing_passive_user:
            # Ask if user wants to take over the passive user account
            log_conversation_step(user_id, "registration", "passive_user_found")
            data, message_takeover = await self.send_keyboard_and_wait_response(
                conv,
                user_id,
                f"Found existing user: **{existing_passive_user.first_name} {existing_passive_user.last_name}**\n\nDo you want to take over this user?",
                KeyboardManager.get_confirmation_keyboard(),
                60
            )
            if data is None:
                await self.api.message_manager.send_text(
                    user_id,
                    "❌ Takeover confirmation timed out. Registration cancelled.",
                    True,
                    True
                )
                return False
            
            if data == "Yes":
                await self.send_or_edit_message(user_id, "✅ Taking over existing user...", message_takeover, remove_buttons=True)
                log_conversation_step(user_id, "registration", "converting_passive_to_telegram_user")
                
                try:
                    # Convert passive user to full user
                    new_user = await handlers.convert_passive_to_telegram_user(
                        passive_user=existing_passive_user,
                        user_id=user_id,
                        username=username,
                        first_name=first_name,
                        last_name=last_name,
                        phone=phone,
                        photo_id=photo_id,
                        lang_code=lang_code
                    )
                    
                    log_conversation_step(user_id, "registration", "passive_user_converted_successfully")
                    await self.api.message_manager.send_keyboard(
                        user_id,
                        f"✅ User takeover successful! Thanks for registering, {first_name}!\nYour display name: **{new_user.display_name}**",
                        KeyboardManager.get_persistent_keyboard(),
                        True,
                        True
                    )
                    log_conversation_completed(user_id, "registration")
                    return True
                    
                except Exception as e:
                    await self.api.message_manager.send_text(
                        user_id,
                        "❌ Failed to take over existing user. Please try again or contact admin.",
                        True,
                        True
                    )
                    return False
            else:
                # User declined takeover
                log_conversation_step(user_id, "registration", "user_declined_takeover")
                await self.send_or_edit_message(user_id, "❌ You declined the user takeover. Ask an admin, if you need help.", message_takeover, remove_buttons=True)
                return False
        
        # Create the user in the database (normal registration or after declining takeover)
        try:
            log_conversation_step(user_id, "registration", "creating_user_in_database")
            new_user = await handlers.register_user(
                user_id=user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                phone=phone,
                photo_id=photo_id,
                lang_code=lang_code
            )
            
            log_conversation_step(user_id, "registration", "user_created_successfully")
            await self.api.message_manager.send_keyboard(
                user_id,
                f"✅ Registration successful! Welcome {first_name}!",
                KeyboardManager.get_persistent_keyboard(),
                True,
                True
            )
            
            log_conversation_completed(user_id, "registration")
            return True
            
        except DuplicateKeyError as e:
            # Handle duplicate key error specifically - usually means phone number already exists
            error_message = str(e)
            # TODO: remove this, does not matter
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
                
                authenticated = await handlers.check_password(password)
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
                log_conversation_timeout(chat_id, "registration", "password_authentication")
                await self.api.message_manager.send_text(
                    chat_id, 
                    "⏱️ Password entry timed out. Registration aborted.", 
                    True, 
                    True
                )
                return False
                
        return False
    
    @managed_conversation("group_selection", 180)
    async def group_selection(self, user_id: int, conv: Conversation, state: ConversationState) -> bool:
        """
        Start the group selection conversation with a user using integrated session management.
        
        This handles the interactive coffee ordering interface with:
        - Session-based group management with integrated GroupState
        - Real-time synchronization across all participants
        - Per-participant pagination state
        - Dynamic keyboard updates via GroupKeyboardManager
        
        Args:
            user_id: Telegram user ID for the conversation
            conv: Active Telethon conversation object (provided by decorator)
            state: Conversation state object (provided by decorator)
            
        Returns:
            bool: True if session completed successfully, False otherwise
        """
        try:
            # Use SessionManager to start or join a session
            session, is_new_session = await self.api.session_manager.start_or_join_session(user_id)
            
            if is_new_session:
                await self.api.message_manager.send_text(
                    user_id,
                    "☕ **New coffee session started!**\n"
                    "Other users can join by typing `/order`",
                    True, True
                )
            else:
                await self.api.message_manager.send_text(
                    user_id,
                    "👥 **Joined existing coffee session!**\n"
                    f"Session has {len(session.participants)} participants",
                    True, True
                )
            
        except ValueError as e:
            await self.api.message_manager.send_text(
                user_id,
                f"❌ Failed to start/join session: {str(e)}",
                True, True
            )
            return False
        except Exception as e:
            await self.api.message_manager.send_text(
                user_id,
                f"❌ Unexpected error: {str(e)}",
                True, True
            )
            return False
        
        # Use GroupKeyboardManager to create and send the keyboard
        message = await self.api.group_keyboard_manager.create_and_send_keyboard(
            user_id, session, initial_page=0
        )
        
        if not message:
            await self.api.message_manager.send_text(
                user_id,
                "❌ Failed to create group selection interface",
                True, True
            )
            return False
            
        submitted = False
        canceled = False

        while True:
            button_data = await self.receive_button_response(conv, user_id, 180)
            
            if button_data is None:
                break                   
            if button_data == "group_submit":
                await message.edit("✅ **Submitted!**", buttons=None)
                submitted = True
                break
            elif button_data == "group_cancel":
                await message.edit("❌ **Cancelled**", buttons=None)
                canceled = True
                break
            
            elif "group_plus" in button_data:
                name = button_data.split("_")[2]
                # Update session and sync all keyboards
                await self.api.session_manager.update_session_member_coffee(
                    name, 'add'
                )
            elif "group_minus" in button_data:
                name = button_data.split("_")[2]
                # Update session and sync all keyboards
                await self.api.session_manager.update_session_member_coffee(
                    name, 'remove'
                )
            elif "group_reset" in button_data:
                name = button_data.split("_")[2]
                # Reset this member's count to 0 and sync keyboards
                await self.api.group_keyboard_manager.handle_member_reset(session, name)
            elif button_data == "group_show_archived":
                # Toggle show_archived to reveal archived users
                await self.api.group_keyboard_manager.handle_show_archived(session, user_id)
            elif button_data == "group_info":
                # Non-actionable label/indicator; ignore
                pass
                
            elif "group_next" in button_data:
                await self.api.group_keyboard_manager.handle_pagination(
                    session, user_id, 'next'
                )
            elif "group_prev" in button_data:  
                await self.api.group_keyboard_manager.handle_pagination(
                    session, user_id, 'prev'
                )
                
        # Unregister the keyboard when conversation ends
        self.api.group_keyboard_manager.unregister_keyboard(user_id, str(session.id))
            
        if submitted:
            # Use SessionManager to complete the session
            await self.api.session_manager.complete_session(user_id)
            log_conversation_completed(user_id, "group_selection")
            return True
            
        elif canceled:
            log_conversation_cancelled(user_id, "group_selection", "user_cancelled")

            # Finalize cancel: remove this user from the session participants
            # and if this was the last active participant, cancel the whole session.
            try:
                await self.api.session_manager.remove_participant(user_id)
            except ValueError as e:
                print(f"Failed to remove participant {user_id} from session: {e}")
                # Check if any active participants/keyboards remain; cancel session if none
            
            try:
                session_id = str(session.id)
                kb_count = self.api.group_keyboard_manager.get_session_participant_count(session_id)
                participants_remaining = len(session.participants)

                if kb_count == 0 or participants_remaining == 0:
                    await self.api.session_manager.cancel_session()

            except Exception as e:
                print(f"Error finalizing cancel for participant {user_id}: {e}")
            
            return False
        
        # Timeout or other exit
        return False
                

    @managed_conversation("add_passive_user", 60)
    async def add_passive_user_conversation(self, user_id: int, conv: Conversation, state: ConversationState) -> bool:
        """Admin-only conversation to add a new passive user."""
        
        # Get first name (with retry)
        while True:
            first_name_event = await self.send_text_and_wait_message(
                conv, user_id, "**First name** of the new user:", 60
            )
            first_name = first_name_event.message.message.strip().title()
            
            if first_name and len(first_name) >= 2:
                break
            
            await self.api.message_manager.send_text(
                user_id, "❌ First name must be at least 2 characters. Try again:", True, True
            )
        
        # Get last name (required)
        last_name_event = await self.send_text_and_wait_message(
            conv, user_id, f"**Last name** for **{first_name}**:", 60
        )
        last_name = last_name_event.message.message.strip().title()
        
        if not last_name or len(last_name) < 2:
            await self.api.message_manager.send_text(
                user_id, "❌ Last name is required and must be at least 2 characters.", True, True
            )
            return False
        
        # Preview with confirmation
        full_name = f"{first_name} {last_name}"
        data, message_confirm = await self.send_keyboard_and_wait_response(
            conv,
            user_id,
            f"**{full_name}**\n\nConfirm creation?",
            KeyboardManager.get_confirmation_keyboard(),
            30
        )
        if data == "No" or data is None:
            await self.send_or_edit_message(user_id, "❌ Creation cancelled.", message_confirm, remove_buttons=True)
            return False
        
        # Create user
        new_user = await handlers.create_passive_user(
            first_name=first_name,
            last_name=last_name
        )
        
        await self.api.message_manager.send_text(
            user_id,
            f"✅ **Created:** {full_name}\n**Display name:** {new_user.display_name}",
            True,
            True
        )
        
        return True

    @managed_conversation("setup_paypal_link", 120, use_existing_conv=True)
    async def setup_paypal_link_subconversation(self, user_id: int, conv: Conversation, state: ConversationState, user, show_current: bool = True) -> bool:
        """
        Reusable subconversation to set up or change PayPal link for a user.
        
        Args:
            user_id: User ID
            conv: Active conversation (provided by decorator)
            state: Conversation state (provided by decorator)
            user: TelegramUser object
            show_current: Whether to show current link and offer change/keep options
            
        Returns:
            True if PayPal link was set up successfully, False otherwise
        """
        # If user has existing link and we should show it, offer options
        if user.paypal_link and show_current:
            from telethon import Button
            
            current_link_text = (
                f"💳 **Current PayPal Link**\n\n"
                f"Your current PayPal link: {user.paypal_link}\n\n"
                f"What would you like to do?"
            )
            
            keyboard = [
                [Button.inline("🔄 Change Link", b"change_link")],
                [Button.inline("✅ Keep Current", b"keep_current")],
                [Button.inline("❌ Cancel", b"cancel")]
            ]
            
            data, message = await self.send_keyboard_and_wait_response(
                conv, user_id, current_link_text, keyboard, 60
            )
            
            if data is None or data == "cancel":
                await self.send_or_edit_message(user_id, "❌ PayPal setup cancelled.", message, remove_buttons=True)
                return False
            
            if data == "keep_current":
                await self.send_or_edit_message(
                    user_id, 
                    f"✅ Keeping your current PayPal link: {user.paypal_link}", 
                    message, 
                    remove_buttons=True
                )
                return True
            
            # If "change_link" is selected, continue to setup
            await self.send_or_edit_message(
                user_id, 
                "🔄 **Changing PayPal Link**\n\nLet's set up your new PayPal link.", 
                message, 
                remove_buttons=True
            )
        else:
            # Show setup message
            if user.paypal_link:
                setup_message = "💳 **PayPal Link Update**\n\nLet's update your PayPal link."
            else:
                setup_message = (
                    "💳 **PayPal Setup Required**\n\n"
                    "To proceed, we need your PayPal information for payments."
                )
            
            await self.api.message_manager.send_text(
                user_id, 
                f"{setup_message}\n\n"
                "Please provide either:\n"
                "• Your PayPal username (e.g., `LukasMandok`)\n"
                "• Your full PayPal.me link (e.g., `https://paypal.me/LukasMandok`)\n\n"
                "ℹ️ Don't know your PayPal.me link? Check: https://www.paypal.com/myaccount/profile/",
                True, True
            )
        
        max_attempts = 3
        attempts = 0
        
        while attempts < max_attempts:
            paypal_event = await self.send_text_and_wait_message(
                conv, user_id, "Enter your PayPal username or PayPal.me link:", 60
            )
            paypal_input = paypal_event.message.message.strip()

            if not paypal_input:
                await self.api.message_manager.send_text(
                    user_id, "❌ PayPal information is required to proceed.", True, True
                )
                return False

            try:
                # Validate & format the PayPal input BEFORE assigning to the model or saving
                logger.info(f"[PayPal Setup] User input: {paypal_input}")
                formatted = create_paypal_link(paypal_input)
                logger.info(f"[PayPal Setup] Normalized link: {formatted}")
                is_valid = False
                validation_error = None
                try:
                    is_valid = validate_paypal_link(formatted)
                except Exception as ve:
                    validation_error = ve
                    logger.error(f"[PayPal Setup] Exception during validation: {ve}", exc_info=True)

                logger.info(f"[PayPal Setup] Validation result: {is_valid}")

                if not is_valid:
                    # Treat as validation failure; do NOT assign or save
                    error_msg = f"PayPal link is not valid or doesn't exist."
                    if validation_error:
                        error_msg += f"\nError: {validation_error}"
                    raise ValueError(error_msg)

                # Only assign and save when validated
                user.paypal_link = formatted
                await user.save()

                await self.api.message_manager.send_text(
                    user_id, f"✅ PayPal link validated and saved: {user.paypal_link}", True, True
                )
                return True

            except Exception as e:
                # Field validation failed or other error; do NOT persist invalid value
                logger.error(f"[PayPal Setup] Exception: {e}", exc_info=True)
                attempts += 1
                remaining = max_attempts - attempts

                error_details = f"\nError: {e}" if e else ""

                if remaining > 0:
                    await self.api.message_manager.send_text(
                        user_id,
                        "❌ The PayPal link you entered is not valid or does not exist.\n\n"
                        "Please check:\n"
                        "• Is your username correct?\n"
                        "• Does your PayPal.me link exist?\n"
                        "• Visit: https://www.paypal.com/myaccount/profile/\n\n"
                        f"You have {remaining} attempt(s) remaining." + error_details,
                        True, True
                    )
                else:
                    await self.api.message_manager.send_text(
                        user_id,
                        "❌ Maximum attempts reached. PayPal setup failed.\n"
                        "Please try again later or contact support." + error_details,
                        True, True
                    )
                    return False
        
        return False



    @managed_conversation("create_coffee_card", 120)
    async def create_coffee_card_conversation(self, user_id: int, conv: Conversation, state: ConversationState) -> bool:
        """Conversation to create a new coffee card with PayPal link setup."""
        
    # Get the user (must be a registered TelegramUser to create coffee cards)
        user = await dep.get_repo().find_user_by_id(user_id)
        if not user or not hasattr(user, 'display_name'):
            await self.api.message_manager.send_text(
                user_id, "❌ Only registered users with display names can create coffee cards.", True, True
            )
            return False
        
        # Check if user has PayPal link, if not, set it up
        if not user.paypal_link:
            paypal_setup_success = await self.setup_paypal_link_subconversation(
                user_id, user=user, show_current=False, existing_conv=conv
            )
            if not paypal_setup_success:
                return False
        
        # Initialize default values
        state.data.update({
            'total_coffees': 200,
            'cost_per_coffee': 0.8
        })
        
        # Show initial overview and get confirmation
        while True:
            total_cost = state.data['total_coffees'] * state.data['cost_per_coffee']
            
            overview_text = (
                f"☕ **Create New Coffee Card**\n\n"
                f"**Total Coffees:** {state.data['total_coffees']}\n"
                f"**Cost per Coffee:** {state.data['cost_per_coffee']:.2f} €\n"
                f"**Total Cost:** {total_cost:.2f} €\n\n"
                f"**PayPal Link:** {user.paypal_link}\n\n"
                f"Confirm creation?"
            )
            
            keyboard = [
                [Button.inline("✅ Yes, Create Card", b"confirm_create")],
                [Button.inline("📝 Adjust Coffees", b"adjust_coffees"), Button.inline("💰 Adjust Price", b"adjust_price")],
                [Button.inline("❌ Cancel", b"cancel")]
            ]
            
            data, message = await self.send_keyboard_and_wait_response(
                conv, user_id, overview_text, keyboard, 60
            )
            
            if data is None or data == "cancel":
                await self.send_or_edit_message(user_id, "❌ Coffee card creation cancelled.", message, remove_buttons=True)
                return False
            
            if data == "confirm_create":
                break
            
            elif data == "adjust_coffees":
                await self.send_or_edit_message(user_id, "Enter new number of coffees:", message, remove_buttons=True)
                
                coffees_event = await self.receive_message(conv, user_id, 60)
                try:
                    new_coffees = int(coffees_event.message.message.strip())
                    if new_coffees <= 0:
                        raise ValueError("Must be positive")
                    state.data['total_coffees'] = new_coffees
                except ValueError:
                    await self.api.message_manager.send_text(
                        user_id, "❌ Invalid number. Using previous value.", True, True
                    )
            
            elif data == "adjust_price":
                await self.send_or_edit_message(user_id, "Enter new price per coffee in €:", message, remove_buttons=True)
                
                price_event = await self.receive_message(conv, user_id, 60)
                try:
                    new_price = float(price_event.message.message.strip())
                    if new_price <= 0:
                        raise ValueError("Must be positive")
                    state.data['cost_per_coffee'] = new_price
                except ValueError:
                    await self.api.message_manager.send_text(
                        user_id, "❌ Invalid price. Using previous value.", True, True
                    )
        
        # Create the coffee card
        try:            
            card = await self.api.coffee_card_manager.create_coffee_card(
                total_coffees=state.data['total_coffees'],
                cost_per_coffee=state.data['cost_per_coffee'],
                purchaser_id=user_id
            )
            
            final_message = (
                f"✅ **Coffee Card Created!**\n\n"
                f"**Total Coffees:** {card.total_coffees}\n"
                f"**Cost per Coffee:** {card.cost_per_coffee:.2f} €\n"
                f"**Total Cost:** {card.total_cost:.2f} €\n\n"
                f"💳 **Payment:** {user.paypal_link}\n\n"
                f"Your coffee card is now active and ready to use!"
            )
            
            await self.send_or_edit_message(user_id, final_message, message, remove_buttons=True)
            return True
            
        except Exception as e:
            await self.send_or_edit_message(
                user_id, f"❌ Failed to create coffee card: {str(e)}", message, remove_buttons=True
            )
            return False

    @managed_conversation("debt_overview", 300)
    async def debt_overview_conversation(self, user_id: int, conv: Conversation, state: ConversationState) -> bool:
        """
        Interactive debt overview conversation with payment marking.
        
        Args:
            user_id: Telegram user ID
            conv: Active Telethon conversation object (provided by decorator)
            state: Conversation state object (provided by decorator)
            
        Returns:
            bool: True if conversation completed successfully, False otherwise
        """
        from telethon import Button
        
        # Get the user
        user = await dep.get_repo().find_user_by_id(user_id)
        if not user:
            await self.api.message_manager.send_text(
                user_id,
                "❌ User not found.",
                True, True
            )
            return False
        
        # Main conversation loop
        message = None
        while True:
            # Get current debt summary
            debt_summary = await self.api.debt_manager.get_debt_summary_by_creditor(user)
            
            if not debt_summary:
                await self.send_or_edit_message(
                    user_id,
                    "✅ **No Outstanding Debts**\n\nYou don't owe anyone money! 🎉",
                    message,
                    remove_buttons=True
                )
                return True
            
            # Build overview text
            overview_text = "💳 **Your Debt Overview**\n\n"
            total_all_debts = 0.0
            creditor_buttons = []
            
            for creditor_name, summary in debt_summary.items():
                total_owed = summary["total_owed"]
                total_all_debts += total_owed
                
                overview_text += f"**{creditor_name}**\n"
                overview_text += f"💰 You owe: **€{total_owed:.2f}**\n"
                
                if summary["paypal_link"]:
                    payment_link_with_amount = f"{summary['paypal_link']}/{total_owed:.2f}EUR"
                    overview_text += f"💳 Pay now: {payment_link_with_amount}\n"
                
                overview_text += "\n"
                creditor_buttons.append(creditor_name)
            
            overview_text += f"**Total Outstanding: €{total_all_debts:.2f}**\n\n"
            overview_text += "━━━━━━━━━━━━━━━━\n"
            overview_text += "**Mark as Paid:**\n"
            overview_text += "Select a creditor below to mark payment"
            
            # Create keyboard with creditor buttons (2 per row)
            buttons = []
            for i in range(0, len(creditor_buttons), 2):
                row = []
                for j in range(2):
                    if i + j < len(creditor_buttons):
                        cred_name = creditor_buttons[i + j]
                        row.append(Button.inline(f"💰 {cred_name}", f"debt_pay:{cred_name}".encode('utf-8')))
                buttons.append(row)
            buttons.append([Button.inline("❌ Close", b"debt_close")])
            
            # Send or edit the overview message
            data, message = await self.send_keyboard_and_wait_response(
                conv, user_id, overview_text, buttons, 180
            )
            
            if data is None or data == "debt_close":
                if message:
                    await message.delete()
                return True
            
            # Handle creditor selection
            if data.startswith("debt_pay:"):
                creditor_name = data.split(":", 1)[1]
                
                if creditor_name not in debt_summary:
                    await self.api.message_manager.send_text(
                        user_id, "❌ Creditor not found.", True, True
                    )
                    continue
                
                # Show payment options
                creditor_info = debt_summary[creditor_name]
                total_owed = creditor_info["total_owed"]
                
                payment_text = (
                    f"💳 **Mark Payment to {creditor_name}**\n\n"
                    f"Total owed: **€{total_owed:.2f}**\n\n"
                    f"Choose an option:"
                )
                
                payment_buttons = [
                    [Button.inline(f"✅ Mark Full Amount (€{total_owed:.2f})", f"debt_mark_full:{creditor_name}".encode('utf-8'))],
                    [Button.inline("💵 Specify Custom Amount", f"debt_mark_custom:{creditor_name}".encode('utf-8'))],
                    [Button.inline("« Back", b"debt_back")]
                ]
                
                data2, message = await self.send_keyboard_and_wait_response(
                    conv, user_id, payment_text, payment_buttons, 120
                )
                
                if data2 is None or data2 == "debt_back":
                    # Return to main overview (loop continues)
                    continue
                
                if data2.startswith("debt_mark_full:"):
                    # Mark full amount as paid
                    cred_name = data2.split(":", 1)[1]
                    
                    # Update all debts for this creditor
                    for debt in creditor_info["debts"]:
                        remaining = debt.total_amount - debt.paid_amount
                        if remaining > 0:
                            debt.paid_amount = debt.total_amount
                            await debt.save()
                    
                    # Show success message
                    await self.send_or_edit_message(
                        user_id,
                        f"✅ Marked €{total_owed:.2f} as paid to {cred_name}",
                        message,
                        remove_buttons=True
                    )
                    
                    # Continue loop to refresh overview
                    message = None
                    continue
                
                if data2.startswith("debt_mark_custom:"):
                    # Custom amount input
                    cred_name = data2.split(":", 1)[1]
                    
                    await self.send_or_edit_message(
                        user_id,
                        f"💵 **Custom Payment to {cred_name}**\n\n"
                        f"Total owed: **€{total_owed:.2f}**\n\n"
                        f"Enter the amount you paid (in €):",
                        message,
                        remove_buttons=True
                    )
                    
                    # Wait for amount input
                    amount_event = await self.receive_message(conv, user_id, 60)
                    try:
                        paid_amount = float(amount_event.message.message.strip())
                        if paid_amount <= 0:
                            raise ValueError("Amount must be positive")
                        if paid_amount > total_owed:
                            raise ValueError("Amount cannot exceed total owed")
                        
                        # Update debts with custom amount
                        remaining_to_pay = paid_amount
                        for debt in creditor_info["debts"]:
                            debt_remaining = debt.total_amount - debt.paid_amount
                            if debt_remaining > 0 and remaining_to_pay > 0:
                                payment_for_this_debt = min(remaining_to_pay, debt_remaining)
                                debt.paid_amount += payment_for_this_debt
                                await debt.save()
                                remaining_to_pay -= payment_for_this_debt
                        
                        await self.api.message_manager.send_text(
                            user_id,
                            f"✅ Marked €{paid_amount:.2f} as paid to {cred_name}",
                            True, True
                        )
                        
                        # Continue loop to refresh overview
                        message = None
                        continue
                        
                    except ValueError as e:
                        await self.api.message_manager.send_text(
                            user_id,
                            f"❌ Invalid amount: {str(e)}",
                            True, True
                        )
                        # Continue loop to return to main overview
                        message = None
                        continue
    
    @managed_conversation("credit_overview", 300)
    async def credit_overview_conversation(self, user_id: int, conv: Conversation, state: ConversationState) -> bool:
        """
        Interactive credit overview conversation for creditors.
        Shows money owed to them and allows marking debts as paid.
        
        Args:
            user_id: Telegram user ID
            conv: Active Telethon conversation object (provided by decorator)
            state: Conversation state object (provided by decorator)
            
        Returns:
            bool: True if conversation completed successfully, False otherwise
        """
        # Get the user
        user = await dep.get_repo().find_user_by_id(user_id)
        if not user:
            await self.api.message_manager.send_text(
                user_id,
                "❌ User not found.",
                True, True
            )
            return False
        
        # Main conversation loop
        message = None
        current_view = "main"  # Can be: main, debtors, debtor_debts
        current_debtor_name = None
        
        while True:
            if current_view == "main":
                # Get all credits (debts where this user is the creditor)
                all_credits = await self.api.debt_manager.get_user_credits(user, include_settled=False)
                
                if not all_credits:
                    await self.send_or_edit_message(
                        user_id,
                        "✅ **No Outstanding Credits**\n\nNo one owes you money! 🎉",
                        message,
                        remove_buttons=True
                    )
                    return True
                
                # Links (debtor, coffee_card) are already fetched by DebtManager
                
                # Group credits by card and calculate totals
                card_summaries = {}  # card_name -> {debtors: {name: amount}, total: float}
                total_all_credits = 0.0
                all_debtors = set()
                
                for debt in all_credits:
                    card_name = debt.coffee_card.name if debt.coffee_card else "Unknown Card"
                    debtor_name = debt.debtor.display_name if debt.debtor else "Unknown"
                    outstanding = debt.total_amount - debt.paid_amount
                    if card_name not in card_summaries:
                        card_summaries[card_name] = {"debtors": {}, "total": 0.0}
                    if debtor_name not in card_summaries[card_name]["debtors"]:
                        card_summaries[card_name]["debtors"][debtor_name] = 0.0
                    card_summaries[card_name]["debtors"][debtor_name] += outstanding
                    card_summaries[card_name]["total"] += outstanding
                    total_all_credits += outstanding
                    all_debtors.add(debtor_name)
                
                # Build overview text
                overview_text = "💰 **Your Credit Overview**\n\n"
                overview_text += "People owe you money from these coffee cards:\n\n"
                
                for card_name, summary in card_summaries.items():
                    overview_text += f"**{card_name}**\n"
                    for debtor_name, amount in summary["debtors"].items():
                        overview_text += f"  • {debtor_name}: €{amount:.2f}\n"
                    overview_text += f"  **Subtotal: €{summary['total']:.2f}**\n\n"
                
                overview_text += f"**Total Owed to You: €{total_all_credits:.2f}**\n\n"
                overview_text += "━━━━━━━━━━━━━━━━\n"
                overview_text += "Choose an action below:"
                
                # Create main action keyboard
                buttons = KeyboardManager.get_credit_main_keyboard()
                
                # Send or edit the overview message
                data, message = await self.send_keyboard_and_wait_response(
                    conv, user_id, overview_text, buttons, 180
                )
                
                if data is None or data == "credit_close":
                    if message:
                        await message.delete()
                    return True
                
                if data == "credit_notify":
                    # Notify all debtors
                    notified_count = 0
                    for debt in all_credits:
                        if debt.debtor and hasattr(debt.debtor, 'user_id'):
                            outstanding = debt.total_amount - debt.paid_amount
                            card_name = debt.coffee_card.name if debt.coffee_card else "Unknown Card"
                            creditor_name = user.display_name
                            notify_text = (
                                f"💳 **Payment Reminder**\n\n"
                                f"You owe **€{outstanding:.2f}** to {creditor_name}\n"
                                f"from coffee card: **{card_name}**\n\n"
                            )
                            if getattr(user, 'paypal_link', None):
                                payment_link = f"{user.paypal_link}/{outstanding:.2f}EUR"
                                notify_text += f"💳 Pay now: {payment_link}"
                            try:
                                await self.api.message_manager.send_text(
                                    debt.debtor.user_id,
                                    notify_text,
                                    True, True
                                )
                                notified_count += 1
                            except Exception as e:
                                logger.error(f"Failed to notify debtor {getattr(debt.debtor, 'user_id', None)}: {e}")
                    
                    await self.send_or_edit_message(
                        user_id,
                        f"✅ Sent {notified_count} payment reminder(s)",
                        message,
                        remove_buttons=True
                    )
                    message = None
                    continue
                
                if data == "credit_mark_paid":
                    current_view = "debtors"
                    continue
            
            elif current_view == "debtors":
                # Show list of debtors to choose from
                all_credits = await self.api.debt_manager.get_user_credits(user, include_settled=False)
                
                # Links already fetched by DebtManager
                
                # Group by debtor
                debtor_totals = {}  # debtor_name -> total_outstanding
                for debt in all_credits:
                    debtor_name = debt.debtor.display_name if debt.debtor else "Unknown"
                    outstanding = debt.total_amount - debt.paid_amount
                    if debtor_name not in debtor_totals:
                        debtor_totals[debtor_name] = 0.0
                    debtor_totals[debtor_name] += outstanding
                
                debtor_list = list(debtor_totals.keys())
                
                debtor_text = "**Select a debtor to mark payments:**\n\n"
                for debtor_name in debtor_list:
                    debtor_text += f"• {debtor_name}: €{debtor_totals[debtor_name]:.2f}\n"
                
                buttons = KeyboardManager.get_credit_debtors_keyboard(debtor_list)
                
                data, message = await self.send_keyboard_and_wait_response(
                    conv, user_id, debtor_text, buttons, 120
                )
                
                if data is None or data == "credit_back_main":
                    current_view = "main"
                    continue
                
                if data.startswith("credit_debtor:"):
                    current_debtor_name = data.split(":", 1)[1]
                    current_view = "debtor_debts"
                    continue
            
            elif current_view == "debtor_debts":
                # Show specific debts for selected debtor
                all_credits = await self.api.debt_manager.get_user_credits(user, include_settled=False)
                
                # Links already fetched by DebtManager
                
                # Filter debts for this debtor
                debtor_debts = []
                for debt in all_credits:
                    debtor_name = debt.debtor.display_name if debt.debtor else "Unknown"
                    if debtor_name == current_debtor_name:
                        outstanding = debt.total_amount - debt.paid_amount
                        if outstanding > 0:
                            card_name = debt.coffee_card.name if debt.coffee_card else "Unknown Card"
                            debtor_debts.append((card_name, outstanding, str(debt.id)))
                
                if not debtor_debts:
                    current_view = "debtors"
                    continue
                
                total_from_debtor = sum(amount for _, amount, _ in debtor_debts)
                
                debt_text = (
                    f"**Debts from {current_debtor_name}**\n\n"
                    f"Total owed: **€{total_from_debtor:.2f}**\n\n"
                    f"Select a debt to mark as paid:"
                )
                
                buttons = KeyboardManager.get_credit_debtor_debts_keyboard(
                    current_debtor_name, debtor_debts
                )
                
                data, message = await self.send_keyboard_and_wait_response(
                    conv, user_id, debt_text, buttons, 120
                )
                
                if data is None or data == "credit_back_debtors":
                    current_view = "debtors"
                    current_debtor_name = None
                    continue
                
                if data.startswith("credit_settle:"):
                    # Mark specific debt as settled
                    debt_id = data.split(":", 1)[1]
                    
                    # Find and settle the debt
                    from beanie import PydanticObjectId
                    from ..models.coffee_models import UserDebt
                    debt = await UserDebt.get(PydanticObjectId(debt_id))
                    
                    if debt:
                        outstanding = debt.total_amount - debt.paid_amount
                        await self.api.debt_manager._apply_payment_to_debt(debt, outstanding)
                        
                        await self.send_or_edit_message(
                            user_id,
                            f"✅ Marked €{outstanding:.2f} as paid by {current_debtor_name}",
                            message,
                            remove_buttons=True
                        )
                    
                    message = None
                    current_view = "debtor_debts"
                    continue
                
                if data.startswith("credit_custom:"):
                    # Custom amount for this debtor
                    await self.send_or_edit_message(
                        user_id,
                        f"💵 **Custom Payment from {current_debtor_name}**\n\n"
                        f"Total owed: **€{total_from_debtor:.2f}**\n\n"
                        f"Enter the amount they paid (in €):",
                        message,
                        remove_buttons=True
                    )
                    
                    # Wait for amount input
                    amount_event = await self.receive_message(conv, user_id, 60)
                    try:
                        paid_amount = float(amount_event.message.message.strip())
                        if paid_amount <= 0:
                            raise ValueError("Amount must be positive")
                        if paid_amount > total_from_debtor:
                            raise ValueError("Amount cannot exceed total owed")
                        
                        # Apply payment to debts from oldest to newest
                        debtor_debts_objects = [
                            debt for debt in all_credits
                            if (debt.debtor and debt.debtor.display_name == current_debtor_name and (debt.total_amount - debt.paid_amount) > 0)
                        ]
                        
                        # Sort by creation date (oldest first)
                        debtor_debts_objects.sort(key=lambda d: d.created_at)
                        
                        remaining_to_apply = paid_amount
                        for debt in debtor_debts_objects:
                            debt_outstanding = debt.total_amount - debt.paid_amount
                            if remaining_to_apply > 0 and debt_outstanding > 0:
                                payment_for_this = min(remaining_to_apply, debt_outstanding)
                                await self.api.debt_manager._apply_payment_to_debt(debt, payment_for_this)
                                remaining_to_apply -= payment_for_this
                        
                        await self.api.message_manager.send_text(
                            user_id,
                            f"✅ Applied €{paid_amount:.2f} payment from {current_debtor_name}",
                            True, True
                        )
                        
                        message = None
                        current_view = "debtor_debts"
                        continue
                        
                    except ValueError as e:
                        await self.api.message_manager.send_text(
                            user_id,
                            f"❌ Invalid amount: {str(e)}",
                            True, True
                        )
                        message = None
                        current_view = "debtor_debts"
                        continue
    
    @managed_conversation("close_card", 60)
    async def close_card_conversation(
        self, 
        user_id: int, 
        conv: Conversation, 
        state: ConversationState,
        card,  # CoffeeCard object
    ) -> bool:
        """
        Handle the conversation flow for completing a coffee card with confirmation.
        
        Args:
            user_id: The user requesting completion
            conv: The active conversation
            state: The conversation state
            card: The CoffeeCard object to complete
            
        Returns:
            True if card was completed, False if cancelled
        """
        from ..models.coffee_models import CoffeeCard
        
        # Check if confirmation is needed (card has remaining coffees)
        if card.remaining_coffees > 0:
            confirmation_text = (
                f"⚠️ Card **{card.name}** has {card.remaining_coffees} coffees left.\n\n"
                f"Complete it anyway?"
            )
            
            # Use existing confirmation keyboard and wait for response
            data, message = await self.send_keyboard_and_wait_response(
                conv,
                user_id,
                confirmation_text,
                KeyboardManager.get_confirmation_keyboard(),
                60
            )
            
            if data != "Yes":
                # User declined or timeout
                await self.send_or_edit_message(
                    user_id,
                    "❌ Cancelled.",
                    message,
                    remove_buttons=True
                )
                return False
            
            # User confirmed - continue with completion
            await self.send_or_edit_message(
                user_id,
                "✅ Completing...",
                message,
                remove_buttons=True
            )
        
        # Now complete the card (without confirmation since we already handled it)
        debts = await self.api.coffee_card_manager.close_card(
            card,
            requesting_user_id=user_id,
            require_confirmation=False  # We already handled confirmation above
        )
        
        return len(debts) > 0  # True if debts were created, False otherwise

    @managed_conversation("settings", 180)
    async def settings_conversation(self, user_id: int, conv: Conversation, state: ConversationState) -> bool:
        """
        Handle the settings conversation flow where users can adjust their preferences.
        
        Args:
            user_id: User ID
            conv: Active conversation (provided by decorator)
            state: Conversation state (provided by decorator)
            
        Returns:
            True if settings were accessed successfully, False otherwise
        """
        from ..dependencies.dependencies import get_repo
        
        # Get current user settings
        repo = get_repo()
        settings = await repo.get_user_settings(user_id)
        
        if not settings:
            await self.api.message_manager.send_text(
                user_id, 
                "❌ Failed to load your settings. Please try again later.",
                True, True
            )
            return False
        
        # Main settings menu
        message = None
        while True:
            # Use SettingsManager to generate menu
            settings_text = self.settings_manager.get_main_menu_text()
            keyboard = self.settings_manager.get_main_menu_keyboard()
            
            # First time: send message, subsequent times: edit message
            if message is None:
                data, message = await self.send_keyboard_and_wait_response(
                    conv, user_id, settings_text, keyboard, 120
                )
            else:
                data, message = await self.edit_keyboard_and_wait_response(
                    conv, user_id, settings_text, keyboard, message, 120
                )
            
            if data is None or data == "done":
                await self.send_or_edit_message(
                    user_id, 
                    "✅ Settings saved!", 
                    message, 
                    remove_buttons=True
                )
                return True
            
            if data == "ordering":
                # Ordering settings submenu
                success = await self._settings_ordering_submenu(user_id, conv, settings, message)
                if not success:
                    return False
                # Reload settings after update
                settings = await repo.get_user_settings(user_id)
                
            elif data == "vanishing":
                # Vanishing messages submenu
                success = await self._settings_vanishing_submenu(user_id, conv, settings, message)
                if not success:
                    return False
                # Reload settings after update
                settings = await repo.get_user_settings(user_id)
                
            elif data == "admin":
                # Administration submenu (placeholder for now)
                await self.send_or_edit_message(
                    user_id,
                    "🔧 **Administration**\n\n"
                    "⚠️ This feature is currently under development.\n"
                    "Administration features will be available for users with admin rights.",
                    message,
                    remove_buttons=True
                )
                # Short delay before returning to main menu
                await asyncio.sleep(2)

    async def _settings_ordering_submenu(self, user_id: int, conv: Conversation, settings, message) -> bool:
        """
        Handle the ordering settings submenu.
        
        Args:
            user_id: User ID
            conv: Active conversation
            settings: Current user settings
            message: Message to edit
            
        Returns:
            True if successful, False otherwise
        """
        from ..dependencies.dependencies import get_repo
        
        while True:
            # Use SettingsManager to generate submenu
            ordering_text = self.settings_manager.get_ordering_submenu_text(settings)
            keyboard = self.settings_manager.get_ordering_submenu_keyboard()
            
            # Edit the existing message instead of sending a new one
            data, message = await self.edit_keyboard_and_wait_response(
                conv, user_id, ordering_text, keyboard, message, 120
            )
            
            if data is None or data == "back":
                return True
            
            if data == "page_size":
                # Page size adjustment flow
                success = await self._settings_page_size_flow(user_id, conv, settings, message)
                if not success:
                    return False
                # Reload settings after update
                repo = get_repo()
                settings = await repo.get_user_settings(user_id)
                
            elif data == "sorting":
                # Sorting adjustment flow
                success = await self._settings_sorting_flow(user_id, conv, settings, message)
                if not success:
                    return False
                # Reload settings after update
                repo = get_repo()
                settings = await repo.get_user_settings(user_id)

    async def _settings_vanishing_submenu(self, user_id: int, conv: Conversation, settings, message) -> bool:
        """
        Handle the vanishing messages settings submenu.
        
        Args:
            user_id: User ID
            conv: Active conversation
            settings: Current user settings
            message: Message to edit
            
        Returns:
            True if successful, False otherwise
        """
        from ..dependencies.dependencies import get_repo
        
        while True:
            # Use SettingsManager to generate submenu
            vanishing_text = self.settings_manager.get_vanishing_submenu_text(settings)
            keyboard = self.settings_manager.get_vanishing_submenu_keyboard()
            
            # Edit the existing message and get button event for popup notifications
            data, message, event = await self.edit_keyboard_and_wait_response(
                conv, user_id, vanishing_text, keyboard, message, 120, return_event=True
            )
            
            if data is None or data == "back":
                # Answer the callback without notification for back button
                if event:
                    await event.answer()
                return True
            
            if data == "toggle":
                # Toggle vanishing messages on/off
                repo = get_repo()
                new_value = not settings.vanishing_enabled
                updated_settings = await repo.update_user_settings(user_id, vanishing_enabled=new_value)
                
                # Answer the callback event
                if event:
                    await event.answer()
                
                if updated_settings:
                    settings = updated_settings
                    status = "enabled" if new_value else "disabled"
                    # Show self-deleting success message
                    await self.api.message_manager.send_text(
                        user_id,
                        f"✅ **Vanishing messages {status}!**",
                        vanish=False,
                        conv=False,
                        delete_after=2
                    )
                else:
                    # Show self-deleting error message
                    await self.api.message_manager.send_text(
                        user_id,
                        "❌ **Failed to update settings**",
                        vanish=False,
                        conv=False,
                        delete_after=3
                    )
                    
            elif data == "threshold":
                # Answer the threshold button
                if event:
                    await event.answer()
                # Adjust vanishing threshold
                success = await self._settings_vanishing_threshold_flow(user_id, conv, settings, message)
                if not success:
                    return False
                # Reload settings after update
                repo = get_repo()
                settings = await repo.get_user_settings(user_id)

    async def _settings_page_size_flow(self, user_id: int, conv: Conversation, settings, message) -> bool:
        """
        Handle the page size adjustment sub-flow.
        
        Args:
            user_id: User ID
            conv: Active conversation
            settings: Current user settings
            message: Message to edit
            
        Returns:
            True if successful, False otherwise
        """
        from ..dependencies.dependencies import get_repo
        
        # Use SettingsManager's generic number input handler
        page_size = await self.settings_manager.get_number_input(
            conv=conv,
            user_id=user_id,
            message_to_edit=message,
            setting_name="Group Page Size",
            description="This setting controls how many users are displayed per page when selecting a group for a coffee order.",
            current_value=settings.group_page_size,
            min_value=5,
            max_value=20
        )
        
        if page_size is None:
            # Cancelled or timeout
            return True
        
        # Update settings (success message already shown by get_number_input)
        repo = get_repo()
        updated_settings = await repo.update_user_settings(user_id, group_page_size=page_size)
        
        if updated_settings:
            return True
        else:
            # Only show error if database update fails
            await self.api.message_manager.send_text(
                user_id,
                "❌ Failed to save settings. Please try again.",
                True, True
            )
            return False

    async def _settings_sorting_flow(self, user_id: int, conv: Conversation, settings, message) -> bool:
        """
        Handle the sorting adjustment sub-flow.
        
        Args:
            user_id: User ID
            conv: Active conversation
            settings: Current user settings
            message: Message to edit
            
        Returns:
            True if successful, False otherwise
        """
        from ..dependencies.dependencies import get_repo
        
        # Use SettingsManager to generate sorting options
        sorting_text = self.settings_manager.get_sorting_options_text(settings)
        keyboard = self.settings_manager.get_sorting_options_keyboard()
        
        # Edit the existing message and get button event
        data, message, event = await self.edit_keyboard_and_wait_response(
            conv, user_id, sorting_text, keyboard, message, 60, return_event=True
        )
        
        if data is None or data == "back":
            # Answer the callback without notification for back button
            if event:
                await event.answer()
            return True
        
        # Answer the callback event
        if event:
            await event.answer()
        
        # Update settings
        repo = get_repo()
        updated_settings = await repo.update_user_settings(user_id, group_sort_by=data)
        
        if updated_settings:
            sort_name = "Alphabetical" if data == "alphabetical" else "Coffee Count"
            # Show self-deleting success message
            await self.api.message_manager.send_text(
                user_id,
                f"✅ **Sorting set to {sort_name}!**",
                vanish=False,
                conv=False,
                delete_after=2
            )
            return True
        else:
            # Show self-deleting error message
            await self.api.message_manager.send_text(
                user_id,
                "❌ **Failed to update settings**",
                vanish=False,
                conv=False,
                delete_after=3
            )
            return False

    async def _settings_vanishing_threshold_flow(self, user_id: int, conv: Conversation, settings, message) -> bool:
        """
        Handle the vanishing threshold adjustment sub-flow.
        
        Args:
            user_id: User ID
            conv: Active conversation
            settings: Current user settings
            message: Message to edit
            
        Returns:
            True if successful, False otherwise
        """
        from ..dependencies.dependencies import get_repo
        
        # Use SettingsManager's generic number input handler
        threshold = await self.settings_manager.get_number_input(
            conv=conv,
            user_id=user_id,
            message_to_edit=message,
            setting_name="Vanishing Threshold",
            description="This setting controls after how many messages or conversations old messages will automatically vanish to keep your chat clean.",
            current_value=settings.vanishing_threshold,
            min_value=1,
            max_value=10
        )
        
        if threshold is None:
            # Cancelled or timeout
            return True
        
        # Update settings (success message already shown by get_number_input)
        repo = get_repo()
        updated_settings = await repo.update_user_settings(user_id, vanishing_threshold=threshold)
        
        if updated_settings:
            return True
        else:
            # Only show error if database update fails
            await self.api.message_manager.send_text(
                user_id,
                "❌ Failed to save settings. Please try again.",
                True, True
            )
            return False

