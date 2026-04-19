"""
Conversation management for Telegram bot.

This module handles multi-step conversation flows including user registration,
authentication, and group selection processes.
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Dict, Any, Callable, Optional, Union, Tuple, overload, Literal
from functools import wraps
from pydantic import BaseModel, Field, ValidationError
import pydantic_core
from telethon import events, Button
from telethon.tl.custom.conversation import Conversation
from pymongo.errors import DuplicateKeyError

from ..handlers import users
from ..handlers.paypal import create_paypal_link, validate_paypal_link
from ..dependencies.dependencies import get_repo
from .settings_manager import SettingsManager
from .keyboards import KeyboardManager
from .credit_flow import run_credit_flow
from .conversation_flows.debt_flow import run_debt_flow
from .conversation_flows.card_flow import create_card_menu_flow, create_close_card_flow, create_new_card_flow
from .conversation_flows.quick_order_flow import create_quick_order_flow, format_not_enough_coffees_text
from .conversation_flows.snapshots_flow import create_snapshots_flow
from .conversation_flows.users_flow import create_users_flow
from .conversation_flows.feedback_flow import create_feedback_flow
from .conversation_flows.session_flow import create_session_flow, KEY_SESSION_OBJ_ID
from .message_flow_helpers import CommonFlowKeys, IntegerParser, MoneyParser
from .command_catalog import COMMAND_BY_CONTEXT

from ..exceptions.coffee_exceptions import CoffeeSessionError, NoActiveCoffeeCardsError

from ..common.log import (
    Logger,
    get_known_loggers, LOG_STATE_SEQUENCE, LOG_STATE_ICON, format_log_state,
)

if TYPE_CHECKING:
    from ..api.telethon_api import TelethonAPI
    from ..models.base_models import TelegramUser

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
                        self.logger.info(
                            f"conversation_cancelled (user_id={user_id}, type={conversation_type}, reason=user_cancelled)",
                            extra_tag="CONV",
                        )
                        return False
                else:
                    # Open a new Telethon conversation and pass it into the wrapped function
                    async with self.api.bot.conversation(user_id) as conv:
                        state.conv = conv
                        try:
                            result = await func(self, user_id, conv, state, *args, **kwargs)
                            return result
                        except ConversationCancelledException:
                            self.logger.info(
                                f"conversation_cancelled (user_id={user_id}, type={conversation_type}, reason=user_cancelled)",
                                extra_tag="CONV",
                            )
                            return False
            except asyncio.TimeoutError:
                # Telethon raises TimeoutError for both real timeouts and a cancelled
                # conversation (e.g. when /cancel triggers conv.cancel()).
                if state.cancelled:
                    self.logger.info(
                        f"conversation_cancelled (user_id={user_id}, type={conversation_type}, reason=user_cancelled)",
                        extra_tag="CONV",
                    )
                    return False

                # Mark that a timeout occurred and handle cleanup centrally
                timed_out = True
                await self.handle_timeout_abort(
                    user_id,
                    conversation_type,
                    clear_latest_messages=True,
                )
                return False
            except Exception as e:
                # Log unexpected errors and return False to the caller
                self.logger.error(
                    f"managed_conversation failed (type={conversation_type}, user_id={user_id})",
                    extra_tag="CONV",
                    exc=e,
                )
                return False
            finally:
                # Only clean up if not timed out (let exception handler handle timeouts)
                # For sub-conversations using existing_conv, don't remove state - parent will handle it
                if not timed_out and not (use_existing_conv and existing_conv is not None):
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
        # Initialize logger with class name
        self.logger = Logger("ConversationManager")
        # Cache repository instance for consistent access
        self.repo = get_repo()
    
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
        self.logger.info(f"Created conversation state for user {user_id}: {conversation_type}")
        
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
            self.logger.info(f"Removed conversation state for user {user_id}")
            
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
            self.logger.debug(f"Updated conversation step for user {user_id} to: {step}")
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
                    self.logger.info(f"Cancelled Telethon conversation for user {user_id}", extra_tag="Telegram")
                except Exception as e:
                    self.logger.error(f"Error cancelling Telethon conversation for user {user_id}", extra_tag="Telegram", exc=e)
            
            # Remove from active conversations (this will also show the persistent keyboard)
            self.remove_conversation_state(user_id)

            self.logger.info(
                f"conversation_cancelled (user_id={user_id}, step={conversation_state.step}, reason=user_cancelled)",
                extra_tag="CONV",
            )
            
            return True
        
        self.logger.warning(f"No active conversation found for user {user_id} to cancel")
        return False

    async def handle_timeout_abort(
        self,
        user_id: int,
        context: str,
        *,
        current_message: Any = None,
        clear_latest_messages: bool = False,
    ) -> None:
        """Apply consistent timeout handling: close stale UI, notify, and clear state."""
        self.logger.warning(
            f"conversation_timeout (user_id={user_id}, context={context}, reason=inactivity)",
            extra_tag="CONV",
        )

        if clear_latest_messages:
            try:
                await self.api.message_manager.clear_user_messages(user_id)
            except Exception:
                pass
        elif current_message is not None:
            try:
                await current_message.delete()
            except Exception:
                pass

        restart_command = COMMAND_BY_CONTEXT.get(context)
        timeout_text = "⏱️ Conversation aborted due to inactivity."
        if restart_command:
            timeout_text = f"{timeout_text}\nUse {restart_command} to start again."

        try:
            await self.api.message_manager.send_text(
                user_id,
                timeout_text,
                vanish=True,
                conv=True,
                delete_after=300,
            )
        except Exception:
            pass

        try:
            self.remove_conversation_state(user_id)
        except Exception:
            pass
    
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
        # Only accept messages from the expected user. This prevents cross-talk
        # if a conversation is ever opened against a shared chat.
        message_event = await conv.wait_event(
            events.NewMessage(incoming=True, from_users=user_id),
            timeout=timeout,
        )
        
        # Check if the received message is a cancel command
        message_text = message_event.message.message.strip()
        if message_text.lower() == '/cancel':
            await message_event.message.delete()  # Delete the command for security
            self.cancel_conversation(user_id)
            raise ConversationCancelledException("Conversation cancelled by user")
        
        return message_event

    # overloaded signatures to help the type checker understand
    @overload
    async def receive_button_response(
        self,
        conv: Conversation,
        user_id: int,
        timeout: int = 60,
        return_event: Literal[False] = False,
    ) -> Optional[str]:
        ...

    @overload
    async def receive_button_response(
        self,
        conv: Conversation,
        user_id: int,
        timeout: int = 60,
        return_event: Literal[True] = True,
    ) -> Tuple[Optional[str], Any]:
        ...

    async def receive_button_response(
        self,
        conv: Conversation,
        user_id: int,
        timeout: int = 60,
        return_event: bool = False,
    ) -> Union[Optional[str], Tuple[Optional[str], Any]]:
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
            self.logger.trace(f"callback_received (user_id={user_id}, data={data})", extra_tag="TELEGRAM")

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
        except asyncio.TimeoutError:
            raise
        except Exception as e:
            # Return the message object even if button response failed
            # so caller can still use it for error handling
            return None, message

    async def edit_keyboard_and_wait_response(
        self,
        conv: Conversation,
        user_id: int,
        message_text: str,
        keyboard,
        message_to_edit: Any,
        timeout: int = 60,
        return_event: bool = False,
    ) -> Tuple[Optional[str], Any, Optional[Any]]:
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
            # Edit the message with new text and keyboard.
            # Some flows send an empty list when there are no buttons which
            # Telegram treats as invalid markup; in that case omit the
            # `buttons` parameter entirely (which leaves the existing
            # keyboard untouched) or explicitly clear it by passing None.
            if not keyboard:
                # either [] or None -> remove buttons
                await self.api.message_manager.edit_message(message_to_edit, message_text, buttons=None)
            else:
                await self.api.message_manager.edit_message(message_to_edit, message_text, buttons=keyboard)

            # Wait for button response
            if return_event:
                data, event = await self.receive_button_response(conv, user_id, timeout, return_event=True)
                return data, message_to_edit, event
            else:
                data = await self.receive_button_response(conv, user_id, timeout)
                # always return a triple for consistent unpacking; caller may ignore third element
                return data, message_to_edit, None
        except asyncio.TimeoutError:
            raise
        except Exception as e:
            # Return the message object even if button response failed
            self.logger.error(f"Error in edit_keyboard_and_wait_response", exc=e)
            if return_event:
                return None, message_to_edit, None
            return None, message_to_edit, None

    async def send_or_edit_message(self, user_id: int, text: str, message_to_edit: Optional[Any] = None, remove_buttons: bool = False, delete_after: int = 0):
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
        if not text.strip():
            self.logger.trace("Skipping send_or_edit_message due to empty text", extra_tag="Telegram")
            return

        if message_to_edit:
            try:
                if remove_buttons:
                    await self.api.message_manager.edit_message(
                        message_to_edit,
                        text,
                        buttons=None,
                        delete_after=delete_after,
                    )
                else:
                    await self.api.message_manager.edit_message(
                        message_to_edit,
                        text,
                        delete_after=delete_after,
                    )
            except Exception as e:
                # If editing fails, fall back to sending new message
                self.logger.warning(f"Failed to edit message, sending new one", exc=e, extra_tag="Telegram")
                await self.api.message_manager.send_text(user_id, text, True, True, delete_after=delete_after)
        else:
            await self.api.message_manager.send_text(user_id, text, True, True, delete_after=delete_after)

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
            self.logger.info(
                f"conversation_cancelled (user_id={user_id}, type=registration, reason=user_declined)",
                extra_tag="CONV",
            )
            return False
            
        await self.send_or_edit_message(user_id, "Start Register process.", message_register, remove_buttons=True)

        # Update conversation state
        self.update_conversation_step(user_id, "password_authentication")

        # Password request
        if not await self.request_authentication(conv):
            return False

        # Get user information from Telegram
        self.logger.trace(
            f"conversation_step (user_id={user_id}, type=registration, step=fetching_user_info)",
            extra_tag="CONV",
        )
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
        self.logger.debug(f"Telegram user entity for user_id {user_id}:", extra_tag="Telegram")
        self.logger.debug(f"  - username: {username}", extra_tag="Telegram")
        self.logger.debug(f"  - first_name: {first_name}", extra_tag="Telegram")
        self.logger.debug(f"  - last_name: {last_name}", extra_tag="Telegram")
        self.logger.debug(f"  - phone: {phone} (type: {type(phone)})", extra_tag="Telegram")
        self.logger.debug(f"  - photo_id: {photo_id}", extra_tag="Telegram")
        self.logger.debug(f"  - lang_code: {lang_code}", extra_tag="Telegram")
        self.logger.debug(f"  - user_entity type: {type(user_entity)}", extra_tag="Telegram")
        
        # Extract photo_id if photo exists
        if photo_id and hasattr(photo_id, 'photo_id'):
            photo_id = photo_id.photo_id
        else:
            photo_id = None
        
        if first_name is None: 
            self.logger.trace(
                f"conversation_step (user_id={user_id}, type=registration, step=first_name_not_found)",
                extra_tag="CONV",
            )
            
            first_name_event = await self.send_text_and_wait_message(
                conv, user_id, 
                "Please provide your first name to complete registration.",
                45
            )
            first_name = first_name_event.message.message.strip().title()
            
        if username is None:
            # combine lowercase first_name and last_name (if available) as username
            username = f"{first_name.lower()}_{last_name.lower()}" if last_name else first_name.lower()
        
        self.logger.trace(
            f"conversation_step (user_id={user_id}, type=registration, step=user_info_retrieved: {first_name} (@{username}))",
            extra_tag="CONV",
        )
        
        # check if a user with this first_name already exists, and in this case require a last name 
        if last_name is None:
            # existing_user = await dep.get_repo().find_user_by_id(user_id)
            # if existing_user and existing_user.first_name.lower() == first_name.lower():
            self.logger.trace(
                f"conversation_step (user_id={user_id}, type=registration, step=user_with_first_name_exists)",
                extra_tag="CONV",
            )
            last_name_event = await self.send_text_and_wait_message(
                conv, user_id,
                "Please provide you last name.",
                45
            )
            last_name = last_name_event.message.message.strip().title()
            
        
        # Check if a passive user with the same name already exists
        # TODO: maybe a search only for the last name 
        self.logger.trace(
            f"conversation_step (user_id={user_id}, type=registration, step=checking_for_existing_passive_user)",
            extra_tag="CONV",
        )
        self.logger.debug(f"Checking for passive user with name: '{first_name}' '{last_name}'")
        existing_passive_user = await users.find_passive_user_by_name(
            first_name=first_name,
            last_name=last_name
        )
        self.logger.debug(f"Found existing passive user: {existing_passive_user}")
        
        if existing_passive_user:
            # Ask if user wants to take over the passive user account
            self.logger.trace(
                f"conversation_step (user_id={user_id}, type=registration, step=passive_user_found)",
                extra_tag="CONV",
            )
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
                self.logger.trace(
                    f"conversation_step (user_id={user_id}, type=registration, step=converting_passive_to_telegram_user)",
                    extra_tag="CONV",
                )
                
                try:
                    # Convert passive user to full user
                    new_user = await users.convert_passive_to_telegram_user(
                        passive_user=existing_passive_user,
                        user_id=user_id,
                        username=username,
                        first_name=first_name,
                        last_name=last_name,
                        phone=phone,
                        photo_id=photo_id,
                        lang_code=lang_code
                    )
                    
                    self.logger.trace(
                        f"conversation_step (user_id={user_id}, type=registration, step=passive_user_converted_successfully)",
                        extra_tag="CONV",
                    )
                    await self.api.message_manager.send_keyboard(
                        user_id,
                        (
                            f"✅ User takeover successful! Thanks for registering, {first_name}!\n"
                            f"Your display name: **{new_user.display_name}**\n\n"
                            f"💡 Getting Started\n"
                            f" - send a number of coffees to quick-order for yourself.\n"
                            f" - enter /order to enter coffees for a larger group.\n"
                            f" - enter /help for a complete overview about all commands.\n"
                        ),
                        KeyboardManager.get_persistent_keyboard(),
                        True,
                        True
                    )
                    self.logger.info(
                        f"conversation_completed (user_id={user_id}, type=registration)",
                        extra_tag="CONV",
                    )
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
                self.logger.trace(
                    f"conversation_step (user_id={user_id}, type=registration, step=user_declined_takeover)",
                    extra_tag="CONV",
                )
                await self.send_or_edit_message(user_id, "❌ You declined the user takeover. Ask an admin, if you need help.", message_takeover, remove_buttons=True)
                return False
        
        # Create the user in the database (normal registration or after declining takeover)
        try:
            self.logger.trace(
                f"conversation_step (user_id={user_id}, type=registration, step=creating_user_in_database)",
                extra_tag="CONV",
            )
            new_user = await users.register_user(
                user_id=user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                phone=phone,
                photo_id=photo_id,
                lang_code=lang_code
            )
            
            self.logger.trace(
                f"conversation_step (user_id={user_id}, type=registration, step=user_created_successfully)",
                extra_tag="CONV",
            )
            await self.api.message_manager.send_keyboard(
                user_id,
                (
                    f"✅ Registration successful! Welcome {first_name}!\n\n"
                    f"💡 Getting Started\n"
                    f" - send a number of coffees to quick-order for yourself.\n"
                    f" - enter /order to order coffees for a larger group.\n"
                    f" - enter /help for a complete overview about all commands.\n"
                ),
                KeyboardManager.get_persistent_keyboard(),
                True,
                True
            )
            
            self.logger.info(
                f"conversation_completed (user_id={user_id}, type=registration)",
                extra_tag="CONV",
            )
            return True
            
        except DuplicateKeyError as e:
            # Handle duplicate key error specifically - usually means phone number already exists
            error_message = str(e)
            # TODO: remove this, does not matter
            if "phone_1 dup key" in error_message:
                self.logger.trace(
                    f"conversation_step (user_id={user_id}, type=registration, step=duplicate_phone_error: {str(e)})",
                    extra_tag="CONV",
                )
                await self.api.message_manager.send_text(
                    user_id,
                    "❌ A user with your phone number is already registered. Please contact your admin.",
                    True,
                    True
                )
            else:
                # Handle other duplicate key errors (e.g., user_id, username)
                self.logger.trace(
                    f"conversation_step (user_id={user_id}, type=registration, step=duplicate_key_error: {str(e)})",
                    extra_tag="CONV",
                )
                await self.api.message_manager.send_text(
                    user_id,
                    "❌ This user account is already registered. Please contact your admin if you believe this is an error.",
                    True,
                    True
                )
            
            self.logger.error(
                f"user_registration failed (user_id={user_id})",
                extra_tag="CONV",
                exc=e,
            )
            return False
            
        except Exception as e:
            self.logger.trace(
                f"conversation_step (user_id={user_id}, type=registration, step=user_creation_failed: {str(e)})",
                extra_tag="CONV",
            )
            await self.api.message_manager.send_text(
                user_id,
                "❌ Registration failed. Please try again later.",
                True,
                True
            )
            
            self.logger.error(
                f"user_registration failed (user_id={user_id})",
                extra_tag="CONV",
                exc=e,
            )
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
                
                authenticated = await users.check_password(password)
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
                self.logger.warning(
                    f"conversation_timeout (user_id={chat_id}, type=registration, step=password_authentication)",
                    extra_tag="CONV",
                )
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
            
            # if is_new_session:
            #     await self.api.message_manager.send_text(
            #         user_id,
            #         "☕ **New coffee session started!**\n"
            #         "Other users can join by typing `/order`",
            #         True, True
            #     )
            if not is_new_session:
                await self.api.message_manager.send_text(
                    user_id,
                    "👥 **Joined existing coffee session!**\n"
                    f"Session has {len(session.participants)} participants",
                    True, True
                )
            
        except NoActiveCoffeeCardsError:
            await self.api.message_manager.send_text(
                user_id,
                "☕ There is currently no active coffee card.\n\n"
                "Please create a new one first with **/new_card** and then try **/order** again.",
                True,
                True,
            )
            return False

        except Exception as e:
            await self.api.message_manager.send_text(
                user_id,
                f"❌ Unexpected error: {str(e)}",
                True, True
            )
            return False
        
        flow = create_session_flow(timeout_seconds=180)
        try:
            completed = await flow.run(
                conv,
                user_id,
                self.api,
                start_state="session_main",
                initial_data={
                    KEY_SESSION_OBJ_ID: session.id,
                },
            )
        finally:
            # Best-effort cleanup: don't leave a stale keyboard reference around.
            try:
                if session.id is not None and bool(session.is_active):
                    self.api.group_keyboard_manager.unregister_keyboard(user_id, str(session.id))
            except Exception:
                pass

        if completed:
            self.logger.info(
                f"conversation_completed (user_id={user_id}, type=group_selection)",
                extra_tag="CONV",
            )
        return completed


    @managed_conversation("quick_order", 45)
    async def quick_order_conversation(self, user_id: int, conv: Conversation, state: ConversationState, quantity: int) -> bool:
        """Quick solo order flow (MessageFlow-based)."""
        initiator = await self.repo.find_user_by_id(user_id)
        if not initiator:
            return False

        cached_available = await self.api.coffee_card_manager.get_available()
        if quantity > cached_available:
            await self.api.coffee_card_manager.load_from_db()

        available = await self.api.coffee_card_manager.get_available()
        if quantity > available:
            await self.api.message_manager.send_text(
                user_id,
                format_not_enough_coffees_text(quantity, int(available)),
                True,
                True,
                delete_after=8,
            )
            return False

        flow = create_quick_order_flow()
        return await flow.run(
            conv,
            user_id,
            self.api,
            start_state="quick_order_confirm",
            initial_data={"quantity": quantity, "initiator": initiator},
        )

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
        new_user = await users.create_passive_user(
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

    @managed_conversation("create_coffee_card", 120)
    async def create_coffee_card_conversation(self, user_id: int, conv: Conversation, state: ConversationState) -> bool:
        """Create a new coffee card (MessageFlow-based)."""
        flow = create_new_card_flow()
        return await flow.run(conv, user_id, self.api, start_state="create_main")

    @managed_conversation("debt", 300)
    async def debt_conversation(self, user_id: int, conv: Conversation, state: ConversationState) -> bool:
        """Interactive debt conversation (MessageFlow-based)."""
        return await run_debt_flow(conv, user_id, self.api)

    @managed_conversation("credit_overview", 300)
    async def credit_overview_conversation(self, user_id: int, conv: Conversation, state: ConversationState) -> bool:
        """
        Interactive credit overview conversation for creditors.
        Shows money owed to them and allows marking debts as paid.
        
        Uses the refactored MessageFlow implementation with helpers for reduced boilerplate.
        
        Args:
            user_id: Telegram user ID
            conv: Active Telethon conversation object (provided by decorator)
            state: Conversation state object (provided by decorator)
            
        Returns:
            bool: True if conversation completed successfully, False otherwise
        """
        
        return await run_credit_flow(conv, user_id, self.api)
    
    @managed_conversation("paypal_setup", 180)
    async def paypal_setup_conversation(self, user_id: int, conv: Conversation, state: ConversationState) -> bool:
        """
        Interactive PayPal link setup conversation.
        Allows users to add, change, or remove their PayPal.me link.
        
        Uses the refactored MessageFlow implementation for simplified flow management.
        
        Args:
            user_id: Telegram user ID
            conv: Active Telethon conversation object (provided by decorator)
            state: Conversation state object (provided by decorator)
            
        Returns:
            bool: True if conversation completed successfully, False otherwise
        """
        from .settings_flow import create_paypal_flow
        
        flow = create_paypal_flow()
        return await flow.run(conv, user_id, self.api, start_state="main")

    @managed_conversation("snapshots", 180)
    async def snapshots_conversation(self, user_id: int, conv: Conversation, state: ConversationState) -> bool:
        """Admin snapshots menu (MessageFlow-based)."""
        flow = create_snapshots_flow()
        return await flow.run(conv, user_id, self.api, start_state="main")

    @managed_conversation("users", 240)
    async def users_conversation(self, user_id: int, conv: Conversation, state: ConversationState) -> bool:
        """Admin users menu (MessageFlow-based)."""
        flow = create_users_flow()
        return await flow.run(conv, user_id, self.api, start_state="main")

    @managed_conversation("feedback", 600)
    async def feedback_conversation(self, user_id: int, conv: Conversation, state: ConversationState) -> bool:
        """Feedback menu (`/feedback`) (MessageFlow-based)."""
        flow = create_feedback_flow()
        return await flow.run(conv, user_id, self.api, start_state="main")
    
    @managed_conversation("close_card", 60)
    async def close_card_conversation(
        self, 
        user_id: int, 
        conv: Conversation, 
        state: ConversationState,
    ) -> bool:
        """Close an existing coffee card (MessageFlow-based)."""
        oldest_card = await self.api.coffee_card_manager.get_oldest_active_coffee_card()
        if not oldest_card:
            await self.api.message_manager.send_text(
                user_id,
                "❌ No active coffee cards found.",
                True,
                True,
            )
            return False
        flow = create_close_card_flow()
        return await flow.run(
            conv,
            user_id,
            self.api,
            start_state="close_confirm",
            initial_data={},
        )

    @managed_conversation("card_menu", 180)
    async def card_menu_conversation(self, user_id: int, conv: Conversation, state: ConversationState) -> bool:
        """Main coffee card menu (`/cards`) (MessageFlow-based)."""
        flow = create_card_menu_flow()
        return await flow.run(conv, user_id, self.api, start_state="menu")

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
        
        # Get current user settings
        user_settings = await self.repo.get_user_settings(user_id)
        
        if not user_settings:
            await self.api.message_manager.send_text(
                user_id, 
                "❌ Failed to load your settings. Please try again later.",
                True, True
            )
            return False
        
        # Main settings menu
        is_admin = await self.repo.is_user_admin(user_id)
        message = None
        while True:
            # Use SettingsManager to generate menu
            settings_text = self.settings_manager.get_main_menu_text()
            keyboard = self.settings_manager.get_main_menu_keyboard(include_admin=is_admin)
            
            # First time: send message, subsequent times: edit message
            if message is None:
                data, message = await self.send_keyboard_and_wait_response(
                    conv, user_id, settings_text, keyboard, 120
                )
            else:
                data, message, _ = await self.edit_keyboard_and_wait_response(
                    conv, user_id, settings_text, keyboard, message, 120
                )
            
            if data is None or data == "done":
                await self.send_or_edit_message(
                    user_id, 
                    "✅ Settings saved!", 
                    message, 
                    remove_buttons=True,
                    delete_after=5,
                )
                return True
            
            if data == "ordering":
                # Ordering settings submenu
                success = await self._settings_ordering_submenu(user_id, conv, user_settings, message)
                if not success:
                    return False
                # Reload settings after update
                user_settings = await self.repo.get_user_settings(user_id)
                
            elif data == "vanishing":
                # Vanishing messages submenu
                success = await self._settings_vanishing_submenu(user_id, conv, user_settings, message)
                if not success:
                    return False
                # Reload settings after update
                user_settings = await self.repo.get_user_settings(user_id)
            
            elif data == "user_notifications":
                # User notification preferences submenu
                success = await self._settings_user_notifications_submenu(user_id, conv, user_settings, message)
                if not success:
                    return False
                # Reload settings after update
                user_settings = await self.repo.get_user_settings(user_id)
                
            elif data == "admin":
                # Check if user is admin
                is_admin = await self.repo.is_user_admin(user_id)
                if not is_admin:
                    await self.send_or_edit_message(
                        user_id,
                        "🔧 **Administration**\n\n"
                        "❌ You need admin rights to access these settings.",
                        message,
                        remove_buttons=True
                    )
                    await asyncio.sleep(2)
                else:
                    # Administration submenu
                    success = await self._settings_admin_submenu(user_id, conv, message)
                    if not success:
                        return False

    async def _settings_user_notifications_submenu(self, user_id: int, conv: Conversation, user_settings, message) -> bool:
        """
        Handle the user notification preferences submenu.
        
        Args:
            user_id: User ID
            conv: Active conversation
            user_settings: Current user settings
            message: Message to edit
            
        Returns:
            True if successful, False otherwise
        """
        
        while True:
            # Get app notification settings to show context
            notification_settings = await self.repo.get_notification_settings()
            if not notification_settings:
                notification_settings = {"notifications_enabled": True, "notifications_silent": False}
            
            # Use SettingsManager to generate user notifications submenu
            notifications_text = self.settings_manager.get_user_notifications_submenu_text(user_settings, notification_settings)
            keyboard = self.settings_manager.get_user_notifications_submenu_keyboard()
            
            # Edit the existing message and get button event
            data, message, event = await self.edit_keyboard_and_wait_response(
                conv, user_id, notifications_text, keyboard, message, 120, return_event=True
            )
            
            if data is None or data == "back":
                # Answer the callback without notification for back button
                if event:
                    await event.answer()
                return True
            
            if data == "toggle_user_notifications":
                new_value = not user_settings.notifications_enabled
                updated_settings = await self.repo.update_user_settings(user_id, notifications_enabled=new_value)

                if event:
                    await event.answer()

                if updated_settings:
                    user_settings = updated_settings
                    status = "enabled" if new_value else "disabled"
                    await self.api.message_manager.send_text(
                        user_id,
                        f"✅ **Notifications {status}!**",
                        vanish=False,
                        conv=False,
                        delete_after=2,
                    )
                else:
                    await self.api.message_manager.send_text(
                        user_id,
                        "❌ **Failed to update settings**",
                        vanish=False,
                        conv=False,
                        delete_after=3,
                    )

            if data == "toggle_user_silent":
                # Toggle user's silent mode preference
                new_value = not user_settings.notifications_silent
                updated_settings = await self.repo.update_user_settings(user_id, notifications_silent=new_value)
                
                # Answer the callback event
                if event:
                    await event.answer()
                
                if updated_settings:
                    user_settings = updated_settings
                    status = "enabled" if new_value else "disabled"
                    # Show self-deleting success message
                    await self.api.message_manager.send_text(
                        user_id,
                        f"✅ **Silent mode {status}!**",
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

    async def _settings_ordering_submenu(self, user_id: int, conv: Conversation, user_settings, message) -> bool:
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
        
        
        while True:
            # Use SettingsManager to generate submenu
            ordering_text = self.settings_manager.get_ordering_submenu_text(user_settings)
            keyboard = self.settings_manager.get_ordering_submenu_keyboard()
            
            # Edit the existing message instead of sending a new one
            data, message, _ = await self.edit_keyboard_and_wait_response(
                conv, user_id, ordering_text, keyboard, message, 120
            )
            
            if data is None or data == "back":
                return True
            
            if data == "page_size":
                # Page size adjustment flow
                success = await self._settings_page_size_flow(user_id, conv, user_settings, message)
                if not success:
                    return False
                # Reload settings after update
                user_settings = await self.repo.get_user_settings(user_id)
                
            elif data == "sorting":
                # Sorting adjustment flow
                success = await self._settings_sorting_flow(user_id, conv, user_settings, message)
                if not success:
                    return False
                # Reload settings after update
                user_settings = await self.repo.get_user_settings(user_id)

    async def _settings_vanishing_submenu(self, user_id: int, conv: Conversation, user_settings, message) -> bool:
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
        
        
        while True:
            # Use SettingsManager to generate submenu
            vanishing_text = self.settings_manager.get_vanishing_submenu_text(user_settings)
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
                assert user_settings is not None
                new_value = not user_settings.vanishing_enabled
                updated_settings = await self.repo.update_user_settings(user_id, vanishing_enabled=new_value)
                
                # Answer the callback event
                if event:
                    await event.answer()
                
                if updated_settings:
                    user_settings = updated_settings
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
                success = await self._settings_vanishing_threshold_flow(user_id, conv, user_settings, message)
                if not success:
                    return False
                # Reload settings after update
                user_settings = await self.repo.get_user_settings(user_id)

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
        updated_settings = await self.repo.update_user_settings(user_id, group_page_size=page_size)
        
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
        updated_settings = await self.repo.update_user_settings(user_id, group_sort_by=data)
        
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

    async def _settings_admin_submenu(self, user_id: int, conv: Conversation, message) -> bool:
        """
        Handle the administration settings submenu (admins only).
        
        Args:
            user_id: User ID
            conv: Active conversation
            message: Message to edit
            
        Returns:
            True if successful, False otherwise
        """
        while True:
            # Use SettingsManager to generate admin submenu
            admin_text = self.settings_manager.get_admin_submenu_text()
            keyboard = self.settings_manager.get_admin_submenu_keyboard()
            
            # Edit the existing message and get button event
            data, message, event = await self.edit_keyboard_and_wait_response(
                conv, user_id, admin_text, keyboard, message, 120, return_event=True
            )
            
            if data is None or data == "back":
                # Answer the callback without notification for back button
                if event:
                    await event.answer()
                return True
            
            if data == "logging":
                # Answer the button
                if event:
                    await event.answer()
                # Logging settings submenu
                success = await self._settings_logging_submenu(user_id, conv, message)
                if not success:
                    return False
            
            elif data == "notifications":
                # Answer the button
                if event:
                    await event.answer()
                # Notifications settings submenu
                success = await self._settings_notifications_submenu(user_id, conv, message)
                if not success:
                    return False

            elif data == "debts":
                if event:
                    await event.answer()
                success = await self._settings_debts_submenu(user_id, conv, message)
                if not success:
                    return False

            elif data == "gsheet":
                if event:
                    await event.answer()
                success = await self._settings_gsheet_submenu(user_id, conv, message)
                if not success:
                    return False

            elif data == "snapshots":
                if event:
                    await event.answer()
                success = await self._settings_snapshots_submenu(user_id, conv, message)
                if not success:
                    return False


    async def _settings_gsheet_submenu(self, user_id: int, conv: Conversation, message) -> bool:
        """Handle the Google Sheets settings submenu (app-wide; admins only)."""
        while True:
            gsheet_settings = await self.repo.get_gsheet_settings()
            if not gsheet_settings:
                await self.api.message_manager.send_text(
                    user_id,
                    "❌ Failed to load Google Sheets settings. Please try again later.",
                    True,
                    True,
                )
                return False

            text = self.settings_manager.get_gsheet_submenu_text(gsheet_settings)
            keyboard = self.settings_manager.get_gsheet_submenu_keyboard(gsheet_settings)

            data, message, event = await self.edit_keyboard_and_wait_response(
                conv, user_id, text, keyboard, message, 120, return_event=True
            )

            if data is None or data == "back":
                if event:
                    await event.answer()
                return True

            if data == "set_period":
                if event:
                    await event.answer()

                current_value = int(gsheet_settings.sync_period_minutes)
                minutes = await self.settings_manager.get_number_input(
                    conv=conv,
                    user_id=user_id,
                    message_to_edit=message,
                    setting_name="Google Sheets Sync Period",
                    description="How often the bot exports the current DB state to Google Sheets (periodic sync).",
                    current_value=current_value,
                    min_value=1,
                    max_value=24 * 60,
                )

                if minutes is None:
                    continue

                success = await self.repo.update_gsheet_settings(sync_period_minutes=minutes)
                if not success:
                    await self.api.message_manager.send_text(
                        user_id,
                        "❌ Failed to save settings. Please try again.",
                        True,
                        True,
                    )
                    return False
                continue

            if data == "toggle_periodic":
                if event:
                    await event.answer()
                new_value = not bool(gsheet_settings.periodic_sync_enabled)
                success = await self.repo.update_gsheet_settings(periodic_sync_enabled=new_value)
                if not success:
                    await self.api.message_manager.send_text(
                        user_id,
                        "❌ Failed to save settings. Please try again.",
                        True,
                        True,
                    )
                    return False
                continue

            if data == "toggle_two_way":
                if event:
                    await event.answer()
                new_value = not bool(getattr(gsheet_settings, "two_way_sync_enabled", False))
                success = await self.repo.update_gsheet_settings(two_way_sync_enabled=new_value)
                if not success:
                    await self.api.message_manager.send_text(
                        user_id,
                        "❌ Failed to save settings. Please try again.",
                        True,
                        True,
                    )
                    return False
                continue

            if data == "toggle_after_actions":
                if event:
                    await event.answer()
                current_value = bool(getattr(gsheet_settings, "sync_after_actions_enabled", True))
                new_value = not current_value
                success = await self.repo.update_gsheet_settings(sync_after_actions_enabled=new_value)
                if not success:
                    await self.api.message_manager.send_text(
                        user_id,
                        "❌ Failed to save settings. Please try again.",
                        True,
                        True,
                    )
                    return False
                continue

            if event:
                await event.answer()

    async def _settings_snapshots_submenu(self, user_id: int, conv: Conversation, message) -> bool:
        """Handle the snapshots settings submenu (app-wide; admins only)."""
        while True:
            snapshot_settings = await self.repo.get_snapshot_settings()
            if not snapshot_settings:
                await self.api.message_manager.send_text(
                    user_id,
                    "❌ Failed to load snapshot settings. Please try again later.",
                    True,
                    True,
                )
                return False

            text = self.settings_manager.get_snapshots_submenu_text(snapshot_settings)
            keyboard = self.settings_manager.get_snapshots_submenu_keyboard(snapshot_settings)

            data, message, event = await self.edit_keyboard_and_wait_response(
                conv, user_id, text, keyboard, message, 120, return_event=True
            )

            if data is None or data == "back":
                if event:
                    await event.answer()
                return True

            if data == "set_keep_last":
                if event:
                    await event.answer()
                current_value = int(getattr(snapshot_settings, "keep_last", 10))
                keep_last = await self.settings_manager.get_number_input(
                    conv=conv,
                    user_id=user_id,
                    message_to_edit=message,
                    setting_name="Snapshots: Keep Last",
                    description="How many committed snapshots are retained (older ones are pruned automatically).",
                    current_value=current_value,
                    min_value=1,
                    max_value=200,
                )
                if keep_last is None:
                    continue
                success = await self.repo.update_snapshot_settings(keep_last=keep_last)
                if not success:
                    await self.api.message_manager.send_text(
                        user_id,
                        "❌ Failed to save settings. Please try again.",
                        True,
                        True,
                    )
                    return False
                continue

            if data == "creation_points":
                if event:
                    await event.answer()

                success = await self._settings_snapshots_creation_points_submenu(user_id, conv, message)
                if not success:
                    return False
                continue

            if event:
                await event.answer()


    async def _settings_snapshots_creation_points_submenu(self, user_id: int, conv: Conversation, message) -> bool:
        """Handle snapshot creation-point toggles (app-wide; admins only)."""
        while True:
            snapshot_settings = await self.repo.get_snapshot_settings()
            if not snapshot_settings:
                await self.api.message_manager.send_text(
                    user_id,
                    "❌ Failed to load snapshot settings. Please try again later.",
                    True,
                    True,
                )
                return False

            text = self.settings_manager.get_snapshots_creation_points_submenu_text(snapshot_settings)
            keyboard = self.settings_manager.get_snapshots_creation_points_submenu_keyboard(snapshot_settings)

            data, message, event = await self.edit_keyboard_and_wait_response(
                conv, user_id, text, keyboard, message, 120, return_event=True
            )

            if data is None or data == "back":
                if event:
                    await event.answer()
                return True

            if data == "toggle_card_closed":
                if event:
                    await event.answer()
                new_value = not bool(getattr(snapshot_settings, "card_closed", True))
                success = await self.repo.update_snapshot_settings(card_closed=new_value)
                if not success:
                    await self.api.message_manager.send_text(
                        user_id,
                        "❌ Failed to save settings. Please try again.",
                        True,
                        True,
                    )
                    return False
                continue

            if data == "toggle_session_completed":
                if event:
                    await event.answer()
                new_value = not bool(getattr(snapshot_settings, "session_completed", True))
                success = await self.repo.update_snapshot_settings(session_completed=new_value)
                if not success:
                    await self.api.message_manager.send_text(
                        user_id,
                        "❌ Failed to save settings. Please try again.",
                        True,
                        True,
                    )
                    return False
                continue

            if data == "toggle_quick_order":
                if event:
                    await event.answer()
                new_value = not bool(getattr(snapshot_settings, "quick_order", False))
                success = await self.repo.update_snapshot_settings(quick_order=new_value)
                if not success:
                    await self.api.message_manager.send_text(
                        user_id,
                        "❌ Failed to save settings. Please try again.",
                        True,
                        True,
                    )
                    return False
                continue

            if data == "toggle_card_created":
                if event:
                    await event.answer()
                new_value = not bool(getattr(snapshot_settings, "card_created", True))
                success = await self.repo.update_snapshot_settings(card_created=new_value)
                if not success:
                    await self.api.message_manager.send_text(
                        user_id,
                        "❌ Failed to save settings. Please try again.",
                        True,
                        True,
                    )
                    return False
                continue

            if event:
                await event.answer()


    async def _settings_debts_submenu(self, user_id: int, conv: Conversation, message) -> bool:
        """Handle the debt settings submenu (app-wide; admins only)."""
        while True:
            debt_settings = await self.repo.get_debt_settings()
            if not debt_settings:
                await self.api.message_manager.send_text(
                    user_id,
                    "❌ Failed to load debt settings. Please try again later.",
                    True, True
                )
                return False

            debts_text = self.settings_manager.get_debts_submenu_text(debt_settings)
            keyboard = self.settings_manager.get_debts_submenu_keyboard()

            data, message, event = await self.edit_keyboard_and_wait_response(
                conv, user_id, debts_text, keyboard, message, 120, return_event=True
            )

            if data is None or data == "back":
                if event:
                    await event.answer()
                return True

            if data == "debt_method":
                if event:
                    await event.answer()

                success = await self._settings_debt_method_flow(user_id, conv, debt_settings, message)
                if not success:
                    return False
                continue

            if data == "debt_threshold":
                if event:
                    await event.answer()
                success = await self._settings_debt_threshold_flow(user_id, conv, debt_settings, message)
                if not success:
                    return False


    async def _settings_debt_method_flow(self, user_id: int, conv: Conversation, debt_settings, message) -> bool:
        """Handle correction method adjustment flow (admins only)."""
        current_method = str(getattr(debt_settings, "correction_method", "absolute") or "absolute").strip().lower()
        current_label = "Absolute" if current_method == "absolute" else "Proportional"

        text = (
            "🧮 **Debt Correction Method**\n\n"
            "This controls how missing-coffee cost is distributed when a card is closed with remaining coffees.\n\n"
            f"**Current method:** {current_label}\n\n"
            "Choose a method:"
        )

        keyboard = [
            [
                Button.inline("🧮 Absolute", b"debt_method_absolute"),
                Button.inline("📊 Proportional", b"debt_method_proportional"),
            ],
            [Button.inline("◁ Back", b"back")],
        ]

        data, _message, event = await self.edit_keyboard_and_wait_response(
            conv,
            user_id,
            text,
            keyboard,
            message,
            120,
            return_event=True,
        )

        if event:
            await event.answer()

        if data is None or data == "back":
            return True

        if data == "debt_method_absolute":
            new_method = "absolute"
        elif data == "debt_method_proportional":
            new_method = "proportional"
        else:
            return True

        success = await self.repo.update_debt_settings(correction_method=new_method)
        if success:
            pretty = "Absolute" if new_method == "absolute" else "Proportional"
            await self.api.message_manager.send_text(
                user_id,
                f"✅ **Debt correction method set to {pretty}!**",
                vanish=False,
                conv=False,
                delete_after=2,
            )
            return True

        await self.api.message_manager.send_text(
            user_id,
            "❌ Failed to save settings. Please try again.",
            True,
            True,
        )
        return False


    async def _settings_debt_threshold_flow(self, user_id: int, conv: Conversation, debt_settings, message) -> bool:
        """Handle correction threshold adjustment flow (admins only)."""
        current_value = int(debt_settings.correction_threshold)

        threshold = await self.settings_manager.get_number_input(
            conv=conv,
            user_id=user_id,
            message_to_edit=message,
            setting_name="Debt Correction Threshold",
            description=(
                "Minimum coffees consumed on a card to participate in the missing-coffee correction. "
                "If a card is completed with remaining coffees, the missing cost is distributed proportionally "
                "among users at or above this threshold."
            ),
            current_value=current_value,
            min_value=0,
            max_value=50
        )

        if threshold is None:
            return True

        success = await self.repo.update_debt_settings(correction_threshold=threshold)
        if success:
            await self.api.message_manager.send_text(
                user_id,
                f"✅ **Debt correction threshold set to {threshold}!**",
                vanish=False,
                conv=False,
                delete_after=2
            )
            return True

        await self.api.message_manager.send_text(
            user_id,
            "❌ Failed to save settings. Please try again.",
            True, True
        )
        return False

    async def _settings_logging_submenu(self, user_id: int, conv: Conversation, message) -> bool:
        """
        Handle the logging settings submenu.
        
        Args:
            user_id: User ID
            conv: Active conversation
            message: Message to edit
            
        Returns:
            True if successful, False otherwise
        """
        
        while True:
            # Get current log settings
            log_settings = await self.repo.get_log_settings()
            if not log_settings:
                await self.api.message_manager.send_text(
                    user_id,
                    "❌ Failed to load logging settings. Please try again later.",
                    True, True
                )
                return False
            
            # Use SettingsManager to generate logging submenu
            logging_text = self.settings_manager.get_logging_submenu_text(log_settings)
            keyboard = self.settings_manager.get_logging_submenu_keyboard()
            
            # Edit the existing message and get button event
            data, message, event = await self.edit_keyboard_and_wait_response(
                conv, user_id, logging_text, keyboard, message, 120, return_event=True
            )
            
            if data is None or data == "back":
                # Answer the callback without notification for back button
                if event:
                    await event.answer()
                return True
            
            if data == "log_level":
                # Answer the button
                if event:
                    await event.answer()
                # Log level selection flow
                success = await self._settings_log_level_flow(user_id, conv, log_settings, message)
                if not success:
                    return False
                    
            elif data == "log_format":
                # Answer the button
                if event:
                    await event.answer()
                # Logging format configuration flow
                success = await self._settings_logging_format_flow(user_id, conv, message)
                if not success:
                    return False

            elif data == "log_modules":
                if event:
                    await event.answer()
                success = await self._settings_logging_modules_flow(user_id, conv, message)
                if not success:
                    return False


    async def _settings_logging_modules_flow(self, user_id: int, conv: Conversation, message) -> bool:
        repo = get_repo()

        per_page = 18
        page = 0

        # Current apply-state (changed via [<-] [State] [->])
        db_log_settings = await repo.get_log_settings() or {}
        global_level = (db_log_settings.get("log_level") or "INFO").upper()
        if global_level not in LOG_STATE_SEQUENCE:
            global_level = "INFO"

        current_state_index = list(LOG_STATE_SEQUENCE).index(global_level) if global_level in LOG_STATE_SEQUENCE else 2

        # Persisted overrides from DB
        existing_overrides: Dict[str, str] = dict(db_log_settings.get("log_module_overrides", {}) or {})

        # Staged (not yet applied) overrides; clicking again reverts.
        pending_overrides: Dict[str, str] = {}

        while True:
            known_loggers = get_known_loggers()
            module_count = len(known_loggers)
            total_pages = max(1, (module_count + per_page - 1) // per_page)
            if page < 0:
                page = 0
            if page >= total_pages:
                page = total_pages - 1

            start = page * per_page
            end = min(start + per_page, module_count)
            page_modules = known_loggers[start:end]

            current_state = LOG_STATE_SEQUENCE[current_state_index]
            current_state_icon = LOG_STATE_ICON.get(current_state, "")

            lines = [
                "🧩 **Module Logging**",
                "",
                f"**Global level:** {format_log_state(global_level)}",
                f"**Apply state:** {format_log_state(current_state)}",
                f"**Pending changes:** {len(pending_overrides)}",
                "",
                "Tap a module to stage that apply state.",
                "Tap it again to revert the staged change.",
                "",
                f"Page {page + 1}/{total_pages} (showing {start + 1 if module_count else 0}-{end} of {module_count})",
            ]

            keyboard: list[list[Any]] = []

            # Modules list (two columns)
            for row_start in range(0, len(page_modules), 2):
                row: list[Any] = []
                for offset in (0, 1):
                    i = row_start + offset
                    if i >= len(page_modules):
                        continue
                    module_name, display_name = page_modules[i]

                    if module_name in pending_overrides:
                        state = pending_overrides[module_name]
                        suffix = " *"
                    elif module_name in existing_overrides:
                        state = existing_overrides[module_name]
                        suffix = ""
                    else:
                        state = global_level
                        suffix = ""

                    icon = LOG_STATE_ICON.get(state, "")
                    label = f"{display_name} {icon}{suffix}".strip()
                    row.append(Button.inline(label, f"m_{i}".encode("utf-8")))
                if row:
                    keyboard.append(row)

            # State selector row (bottom, above pagination)
            keyboard.append(
                [
                    Button.inline("⬅", b"state_prev"),
                    Button.inline(f"{current_state}{current_state_icon}", b"noop"),
                    Button.inline("➡", b"state_next"),
                ]
            )

            nav_row: list[Any] = []
            if total_pages > 1:
                nav_row = [
                    Button.inline("◀ Prev", b"page_prev"),
                    Button.inline(f"{page + 1}/{total_pages}", b"noop"),
                    Button.inline("Next ▶", b"page_next"),
                ]
            else:
                nav_row = [Button.inline("1/1", b"noop")]
            keyboard.append(nav_row)

            keyboard.append(
                [
                    Button.inline(f"{self.settings_manager.ICON_BACK} Back", b"back"),
                    Button.inline("✅ Apply", b"apply"),
                ]
            )

            data, message, event = await self.edit_keyboard_and_wait_response(
                conv, user_id, "\n".join(lines), keyboard, message, 120, return_event=True
            )

            if data is None or data == "back":
                if event:
                    await event.answer()
                return True

            if data == "noop":
                if event:
                    await event.answer()
                continue

            if data == "state_prev":
                if event:
                    await event.answer()
                # Left arrow: less verbose (towards OFF)
                current_state_index = (current_state_index + 1) % len(LOG_STATE_SEQUENCE)
                continue

            if data == "state_next":
                if event:
                    await event.answer()
                # Right arrow: more verbose (towards TRACE)
                current_state_index = (current_state_index - 1) % len(LOG_STATE_SEQUENCE)
                continue

            if data == "page_prev":
                if event:
                    await event.answer()
                page -= 1
                continue

            if data == "page_next":
                if event:
                    await event.answer()
                page += 1
                continue

            if data == "apply":
                if event:
                    await event.answer()

                new_overrides = dict(existing_overrides)
                for module_name, state in pending_overrides.items():
                    new_overrides[module_name] = state

                success = await repo.update_log_settings(log_module_overrides=new_overrides)
                if not success:
                    await self.api.message_manager.send_text(
                        user_id,
                        "❌ Failed to apply module logging settings.",
                        vanish=False,
                        conv=False,
                        delete_after=3,
                    )
                    continue

                return True

            if data.startswith("m_"):
                if event:
                    await event.answer()
                try:
                    idx = int(data.split("_", 1)[1])
                except ValueError:
                    continue

                if idx < 0 or idx >= len(page_modules):
                    continue

                module_name, _display_name = page_modules[idx]
                if module_name in pending_overrides:
                    pending_overrides.pop(module_name, None)
                else:
                    pending_overrides[module_name] = LOG_STATE_SEQUENCE[current_state_index]
                continue

    async def _settings_logging_format_flow(self, user_id: int, conv: Conversation, message) -> bool:
        """
        Handle the logging format configuration with inline toggles.
        
        Args:
            user_id: User ID
            conv: Active conversation
            message: Message to edit
            
        Returns:
            True if successful, False otherwise
        """
        
        
        repo = get_repo()
        
        while True:
            # Get current log settings
            log_settings = await repo.get_log_settings()
            if not log_settings:
                await self.api.message_manager.send_text(
                    user_id,
                    "❌ Failed to load logging settings. Please try again later.",
                    True, True
                )
                return False
            
            # Use SettingsManager to generate format screen
            format_text = self.settings_manager.get_logging_format_text(log_settings)
            keyboard = self.settings_manager.get_logging_format_keyboard(log_settings)
            
            # Edit the existing message and get button event
            data, message, event = await self.edit_keyboard_and_wait_response(
                conv, user_id, format_text, keyboard, message, 120, return_event=True
            )
            
            if data is None or data == "back":
                # Answer the callback without notification for back button
                if event:
                    await event.answer()
                return True
            
            if data == "toggle_time":
                # Toggle time display
                new_value = not log_settings.get("log_show_time", True)
                success = await repo.update_log_settings(log_show_time=new_value)
                
                # Answer callback without notification (UI re-renders with updated button/text)
                if event:
                    await event.answer()
                
                if not success:
                    await self.api.message_manager.send_text(
                        user_id,
                        "❌ **Failed to update settings**",
                        vanish=False,
                        conv=False,
                        delete_after=3
                    )
                # Continue loop to refresh the display
                    
            elif data == "toggle_caller":
                # Toggle caller display
                new_value = not log_settings.get("log_show_caller", True)
                success = await repo.update_log_settings(log_show_caller=new_value)
                
                # Answer callback without notification (UI re-renders with updated button/text)
                if event:
                    await event.answer()
                
                if not success:
                    await self.api.message_manager.send_text(
                        user_id,
                        "❌ **Failed to update settings**",
                        vanish=False,
                        conv=False,
                        delete_after=3
                    )
                # Continue loop to refresh the display
                    
            elif data == "toggle_class":
                # Toggle class name display
                new_value = not log_settings.get("log_show_class", True)
                success = await repo.update_log_settings(log_show_class=new_value)
                
                # Answer callback without notification (UI re-renders with updated button/text)
                if event:
                    await event.answer()
                
                if not success:
                    await self.api.message_manager.send_text(
                        user_id,
                        "❌ **Failed to update settings**",
                        vanish=False,
                        conv=False,
                        delete_after=3
                    )
                # Continue loop to refresh the display

    async def _settings_log_level_flow(self, user_id: int, conv: Conversation, log_settings: Dict, message) -> bool:
        """
        Handle the log level selection sub-flow.
        
        Args:
            user_id: User ID
            conv: Active conversation
            log_settings: Current log settings
            message: Message to edit
            
        Returns:
            True if successful, False otherwise
        """
        
        
        # Use SettingsManager to generate log level options
        current_level = log_settings.get("log_level", "INFO")
        log_level_text = self.settings_manager.get_log_level_options_text(current_level)
        keyboard = self.settings_manager.get_log_level_options_keyboard()
        
        # Edit the existing message and get button event
        data, message, event = await self.edit_keyboard_and_wait_response(
            conv, user_id, log_level_text, keyboard, message, 60, return_event=True
        )
        
        if data is None or data == "back":
            # Answer the callback without notification for back button
            if event:
                await event.answer()
            return True
        
        # Answer the callback event
        if event:
            await event.answer()
        
        # Update log level
        success = await self.repo.update_log_settings(log_level=data)
        
        if success:
            await self.api.message_manager.send_text(
                user_id,
                f"✅ **Log level set to {data}!**",
                vanish=False,
                conv=False,
                delete_after=2
            )
            return True
        else:
            await self.api.message_manager.send_text(
                user_id,
                "❌ **Failed to update settings**",
                vanish=False,
                conv=False,
                delete_after=3
            )
            return False

    async def _settings_notifications_submenu(self, user_id: int, conv: Conversation, message) -> bool:
        """
        Handle the notifications settings submenu (admin only - app-wide settings).
        
        Args:
            user_id: User ID
            conv: Active conversation
            message: Message to edit
            
        Returns:
            True if successful, False otherwise
        """
        
        while True:
            # Get app notification settings
            notification_settings = await self.repo.get_notification_settings()
            if not notification_settings:
                await self.api.message_manager.send_text(
                    user_id,
                    "❌ Failed to load notification settings. Please try again later.",
                    True, True
                )
                return False

            # Use SettingsManager to generate notifications submenu
            notifications_text = self.settings_manager.get_notifications_submenu_text(notification_settings)
            keyboard = self.settings_manager.get_notifications_submenu_keyboard(notification_settings)
            
            # Edit the existing message and get button event
            data, message, event = await self.edit_keyboard_and_wait_response(
                conv, user_id, notifications_text, keyboard, message, 120, return_event=True
            )
            
            if data is None or data == "back":
                # Answer the callback without notification for back button
                if event:
                    await event.answer()
                return True
            
            if data == "toggle_notifications":
                # Toggle app-wide notifications on/off
                new_value = not notification_settings["notifications_enabled"]
                success = await self.repo.update_notification_settings(notifications_enabled=new_value)
                
                # Answer the callback event
                if event:
                    await event.answer()
                
                if success:
                    notification_settings["notifications_enabled"] = new_value
                    status = "enabled" if new_value else "disabled"
                    # Show self-deleting success message
                    await self.api.message_manager.send_text(
                        user_id,
                        f"✅ **Notifications {status} globally!**",
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
                    
            elif data == "toggle_silent":
                # Toggle app-wide silent mode on/off (only available if notifications are enabled)
                if not notification_settings["notifications_enabled"]:
                    if event:
                        await event.answer("❌ Enable notifications first!", alert=True)
                    continue
                
                new_value = not notification_settings["notifications_silent"]
                success = await self.repo.update_notification_settings(notifications_silent=new_value)
                
                # Answer the callback event
                if event:
                    await event.answer()
                
                if success:
                    notification_settings["notifications_silent"] = new_value
                    status = "enabled" if new_value else "disabled"
                    # Show self-deleting success message
                    await self.api.message_manager.send_text(
                        user_id,
                        f"✅ **Global silent mode {status}!**",
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
        updated_settings = await self.repo.update_user_settings(user_id, vanishing_threshold=threshold)
        
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
