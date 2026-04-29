"""
Conversation management for Telegram bot.

This module handles multi-step conversation flows including user registration,
authentication, and group selection processes.
"""

import asyncio
import logging
import time
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
from .credit_flow import run_credit_flow
from .conversation_flows.debt_flow import run_debt_flow
from .conversation_flows.card_flow import create_card_menu_flow, create_close_card_flow, create_new_card_flow
from .conversation_flows.quick_order_flow import create_quick_order_flow, format_not_enough_coffees_text
from .conversation_flows.snapshots_flow import create_snapshots_flow
from .conversation_flows.users_flow import create_users_flow
from .conversation_flows.feedback_flow import create_feedback_flow
from .conversation_flows.session_flow import create_session_flow, KEY_SESSION_OBJ_ID, KEY_IS_NEW_SESSION
from .message_flow import PaginationConfig, build_telethon_pagination_nav_keyboard, paginate_items_0_indexed
from .message_flow_ids import CommonCallbacks
from .message_flow_helpers import (
    CommonFlowKeys,
    IntegerParser,
    MoneyParser,
    pop_notify,
    get_confirmation_keyboard,
    get_keyboard_callback_filter,
    get_persistent_keyboard,
)
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
    last_activity_ts: float = Field(
        default_factory=time.monotonic,
        description="Monotonic timestamp of last user interaction",
    )
    last_activity_event_key: Optional[str] = Field(
        default=None,
        description="Dedup key for last activity event (message/callback)",
    )
    
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

        self.logger.trace(
            f"inactivity_timeout_configured (user_id={user_id}, type={conversation_type}, timeout_s={timeout})",
            extra_tag="CONV",
        )
        
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

    def touch_conversation(
        self,
        user_id: int,
        *,
        reason: str,
        timeout_seconds: Optional[int] = None,
        event_key: Optional[str] = None,
    ) -> None:
        """Update last-activity timestamp for rolling inactivity timeouts."""
        state = self.active_conversations.get(user_id)
        if state is None:
            return

        if event_key is not None and state.last_activity_event_key == event_key:
            return

        now_ts = time.monotonic()
        since_last_s = now_ts - state.last_activity_ts

        state.last_activity_ts = now_ts
        state.last_activity_event_key = event_key

        if timeout_seconds is not None:
            self.logger.trace(
                (
                    f"inactivity_timeout_reset (user_id={user_id}, reason={reason}, "
                    f"timeout_s={timeout_seconds}, since_last_s={since_last_s:.1f})"
                ),
                extra_tag="CONV",
            )
        else:
            self.logger.trace(
                f"inactivity_timeout_reset (user_id={user_id}, reason={reason}, since_last_s={since_last_s:.1f})",
                extra_tag="CONV",
            )
    
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
        while True:
            state = self.active_conversations.get(user_id)
            if state is not None:
                remaining = (state.last_activity_ts + timeout) - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError
            else:
                remaining = timeout

            try:
                # Only accept messages from the expected user. This prevents cross-talk
                # if a conversation is ever opened against a shared chat.
                message_event = await conv.wait_event(
                    events.NewMessage(incoming=True, from_users=user_id),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                # If there was any user interaction since we started waiting,
                # keep waiting and refresh the inactivity window.
                state = self.active_conversations.get(user_id)
                if state is not None and (time.monotonic() - state.last_activity_ts) < timeout:
                    continue
                raise

            msg_id = getattr(message_event.message, "id", None)
            event_key = f"msg:{msg_id}" if msg_id is not None else None
            self.touch_conversation(
                user_id,
                reason="message_received",
                timeout_seconds=timeout,
                event_key=event_key,
            )
            break
        
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
        while True:
            state = self.active_conversations.get(user_id)
            if state is not None:
                remaining = (state.last_activity_ts + timeout) - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError
            else:
                remaining = timeout

            try:
                button_event = await conv.wait_event(
                    get_keyboard_callback_filter(user_id),
                    timeout=remaining
                )
            except asyncio.TimeoutError:
                # If the user interacted but did not produce a matching callback,
                # keep waiting and treat it as activity.
                state = self.active_conversations.get(user_id)
                if state is not None and (time.monotonic() - state.last_activity_ts) < timeout:
                    continue
                raise

            cb_id = getattr(button_event, "id", None)
            event_key = f"cb:{cb_id}" if cb_id is not None else None
            self.touch_conversation(
                user_id,
                reason="callback_received",
                timeout_seconds=timeout,
                event_key=event_key,
            )
            break

        data = button_event.data.decode('utf8')
        self.logger.trace(f"callback_received (user_id={user_id}, data={data})", extra_tag="TELEGRAM")

        # If a short notify_text was registered for this callback, send it now.
        try:
            notify_text = pop_notify(data)
            if notify_text:
                # Send a small transient text message (not a popup)
                await self.api.message_manager.send_text(
                    user_id,
                    notify_text,
                    vanish=False,
                    conv=False,
                    delete_after=2,
                )
        except Exception:
            # Don't let notify failures break the conversation flow
            pass

        if return_event:
            # Don't answer yet - let caller send custom popup notification
            return data, button_event
        else:
            # Answer without notification by default for backward compatibility
            await button_event.answer()
            return data

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
        """Run the registration MessageFlow."""
        from .conversation_flows.registration_flow import create_registration_flow

        flow = create_registration_flow()
        return await flow.run(conv, user_id, self.api, start_state="confirm_register")
            
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
                
                authenticated = await users.check_password(self.repo, password)
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
            # Join-status message is sent by SessionFlow and tracked as an auxiliary
            # message so it gets deleted automatically when the flow exits.
            
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
                    KEY_IS_NEW_SESSION: bool(is_new_session),
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
            get_confirmation_keyboard(),
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
        from .paypal_flow import create_paypal_flow
        
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
        """Delegate to the MessageFlow-based settings implementation in src/bot/settings_flow.py."""
        from .settings_flow import create_settings_flow
        flow = create_settings_flow()
        return await flow.run(conv, user_id, self.api, start_state="main")
