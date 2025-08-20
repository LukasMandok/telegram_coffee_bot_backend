"""
Conversation management for Telegram bot.

This module handles multi-step conversation flows including user registration,
authentication, and group selection processes.
"""

import asyncio
from typing import TYPE_CHECKING, Dict, Any, Callable, Optional
from functools import wraps
from pydantic import BaseModel, Field, ValidationError
import pydantic_core
from telethon import events, Button
from telethon.tl.custom.conversation import Conversation
from pymongo.errors import DuplicateKeyError

from ..handlers import handlers
from ..handlers.coffee import create_coffee_card
from ..handlers.paypal import create_paypal_link, validate_paypal_link
from ..dependencies import dependencies as dep
from .keyboards import KeyboardManager

            
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
        async def wrapper(self, user_id: int, *args, **kwargs):
            # Create and register conversation state
            state = self.create_conversation_state(user_id, conversation_type, timeout)
            try:
                # Open a Telethon conversation and pass it into the wrapped function
                async with self.api.bot.conversation(user_id) as conv:
                    state.conv = conv
                    try:
                        result = await func(self, user_id, conv, state, *args, **kwargs)
                        return result
                    except ConversationCancelledException:
                        # Let the caller handle cancellation messaging if needed
                        log_conversation_cancelled(user_id, conversation_type)
                        return False
            except Exception as e:
                # Log unexpected errors and return False to the caller
                log_unexpected_error(f"managed_conversation_{conversation_type}", str(e), {"user_id": user_id})
                return False
            finally:
                # Clean up conversation state in all cases
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

    async def receive_button_response(self, conv: Conversation, user_id: int, timeout: int = 60) -> Optional[str]:
        """
        Receive a button callback response from a conversation.
        
        This method wraps conv.wait_event for button callbacks and automatically
        handles the response, logging, and data extraction.
        
        Args:
            conv: The active Telethon conversation object
            user_id: The user ID for the conversation
            timeout: Timeout in seconds for waiting for the button response
            
        Returns:
            str: The button data if successful, None if failed
            
        Raises:
            TimeoutError: If no button response is received within timeout
        """
        try:
            button_event = await conv.wait_event(
                KeyboardManager.get_keyboard_callback_filter(user_id),
                timeout=timeout
            )
            await button_event.answer()
            data = button_event.data.decode('utf8')
            log_telegram_callback(user_id, data)
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
                    await message_to_edit.edit(text, buttons=None)
                else:
                    await message_to_edit.edit(text)
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
                log_conversation_step(user_id, "registration", "converting_passive_to_full_user")
                
                try:
                    # Convert passive user to full user
                    new_user = await handlers.convert_passive_to_full_user(
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
                    await self.api.message_manager.send_text(
                        user_id,
                        f"✅ User takeover successful! Thanks for registering, {first_name}!\nYour display name: **{new_user.display_name}**",
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
    
    async def group_selection(self, user_id: int) -> None:
        """
        Start the group selection conversation with a user using integrated session management.
        
        This handles the interactive coffee ordering interface with:
        - Session-based group management with integrated GroupState
        - Real-time synchronization across all participants
        - Per-participant pagination state
        - Dynamic keyboard updates via GroupKeyboardManager
        
        Args:
            user_id: Telegram user ID for the conversation
        """
        try:
            # Use SessionManager to start or join a session
            session, is_new_session = await self.api.session_manager.start_or_join_session(user_id)
            
            if is_new_session:
                await self.api.message_manager.send_text(
                    user_id,
                    "☕ **New coffee session started!**\n"
                    "Other users can join by typing `/group`",
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
            return
        except Exception as e:
            await self.api.message_manager.send_text(
                user_id,
                f"❌ Unexpected error: {str(e)}",
                True, True
            )
            return
        
        # Start the interactive group selection interface
        async with self.api.bot.conversation(user_id) as conv:
            chat = await conv.get_input_chat()
            
            # TODO: maybe use the session_manager insteaad?
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
                return
                
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
                await self.api.session_manager.complete_session(session, user_id)
                log_conversation_completed(user_id, "group_selection")
                
            elif canceled:
                log_conversation_cancelled(user_id, "group_selection", "user_cancelled")
                # Just unregister this user's keyboard, don't affect the session

                
                # TODO: finalize implementation of cancel
                
                # remove user as participant from active session
                try:
                    await session.remove_participant(user_id)
                except ValueError as e:
                    print(f"Failed to remove participant {user_id} from session: {e}")
                    
                #
                

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
        if data is None:
            return False
        
        if data == "No":
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
