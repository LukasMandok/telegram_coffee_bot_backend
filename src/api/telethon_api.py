"""
Telethon API module for Telegram bot functionality.

This module provides a comprehensive Telegram bot implementation using the Telethon library
with Pydantic models for data validation and type safety. It includes features for:

- Message handling and conversation management
- Coffee ordering system with inline keyboards
- User verification and authentication
- Automatic message cleanup
- Pydantic-based configuration and data models

Classes:
    - TelethonAPI: Main bot API handler

Note: Command handlers have been moved to bot/commands.py (CommandManager class).
Note: Keyboard functionality has been moved to bot/keyboards.py (KeyboardManager, KeyboardButton).
Note: Message management functionality has been moved to bot/message_manager.py (MessageManager).
Note: Conversation management has been moved to bot/conversations.py (ConversationManager).
Note: Telegram-specific models have been moved to bot/telethon_models.py.
"""

import asyncio
import uuid
import re
from typing import Callable, Optional, Dict, Union, Any, TYPE_CHECKING

# Runtime imports - actually used at runtime
from telethon import TelegramClient, events, errors

from ..handlers import handlers, exceptions
from ..dependencies import dependencies as dep
from ..bot.telethon_models import ( MessageModel, BotConfiguration )
from ..bot.keyboards import KeyboardButton
from ..common.log import (
    log_telegram_bot_started, log_telegram_command, log_telegram_callback,
    log_telegram_message_sent, log_telegram_api_error, log_unexpected_error
)

from ..bot.message_manager import MessageManager
from ..bot.commands import CommandManager
from ..bot.group_keyboard_manager import GroupKeyboardManager
from ..bot.session_manager import SessionManager

# Type-only imports - only needed for type annotations
if TYPE_CHECKING:
    from ..bot.keyboards import KeyboardManager

# --- Pydantic Models for Type Safety and Data Validation ---
# Models have been moved to bot/telethon_models.py for better organization

### API Handler

class TelethonAPI:
    """
    Main Telegram bot API handler.

    This class manages bot initialization, message handling, user conversations,
    and coffee group ordering through Telegram inline keyboards.
    """
    
    def __init__(self, api_id: Union[int, str], api_hash: str, bot_token: str) -> None:
        """
        Initialize the TelethonAPI bot and set up handlers and state.
        
        Args:
            api_id: Telegram API ID from my.telegram.org (can be string or int)
            api_hash: Telegram API hash from my.telegram.org  
            bot_token: Bot token from @BotFather
        """
        # Convert api_id to int if it's a string
        # IDEA: it should be possible to put this into property or isn't this the idea behind the property setter?
        if isinstance(api_id, str):
            api_id = int(api_id)
            
        # Store configuration using Pydantic model
        self.config = BotConfiguration(
            api_id=api_id,
            api_hash=api_hash,
            bot_token=bot_token
        )

        self.bot: TelegramClient = TelegramClient(
            'bot_' + str(uuid.uuid4()),
            self.config.api_id,
            self.config.api_hash
        ).start(bot_token=self.config.bot_token)
        
        log_telegram_bot_started(self.config.api_id)

        # Initialize managers with runtime imports to avoid circular dependencies
        self.message_manager = MessageManager(self.bot)
        self.command_manager = CommandManager(self)
        self.group_keyboard_manager = GroupKeyboardManager(self)
        self.session_manager = SessionManager(self)

        # Register all handlers
        self._register_handlers()
    
    def _register_handlers(self) -> None:
        """Register all bot event handlers."""
        # Use CommandManager methods directly for cleaner architecture
        self.add_handler(lambda event: self.command_manager.handle_start_command(event), '/start')
        self.add_handler(lambda event: self.command_manager.handle_group_command(event), '/group')
        self.add_handler(lambda event: self.command_manager.handle_password_command(event), '/password')
        self.add_handler(lambda event: self.command_manager.handle_user_verification_command(event), "/user")
        self.add_handler(lambda event: self.command_manager.handle_admin_verification_command(event), "/admin")
        self.add_handler(lambda event: self.command_manager.handle_add_passive_user_command(event), "/add_user")
        self.add_handler(lambda event: self.command_manager.handle_new_coffee_card_command(event), "/new_coffee_card")
        self.add_handler(lambda event: self.command_manager.handle_paypalme_command(event), "/paypalme")
        self.add_handler(lambda event: self.command_manager.handle_cancel_command(event), "/cancel")
        # TODO: check if I actually need them
        self.add_handler(lambda event: self.command_manager.handle_complete_session_command(event), "/complete_session")
        self.add_handler(lambda event: self.command_manager.handle_cancel_session_command(event), "/cancel_session")
        
        self.add_handler(lambda event: self.command_manager.handle_digits_command(event), events.NewMessage(incoming=True, pattern=re.compile(r'[0-9]+')))
        self.add_handler(lambda event: self.command_manager.handle_unknown_command(event))
    
    async def run(self) -> None:
        """
        Start the message vanisher task and run the bot until disconnected.
        
        This method starts background tasks and keeps the bot running
        until manually disconnected or an error occurs.
        """
        asyncio.create_task(self.message_manager.message_vanisher()) 
        
        self.bot.run_until_disconnected()
        
        
    ### SECTION: handler administration
    
    def add_handler(
        self,
        handler: Callable[..., Any],
        event: Optional[Union[str, Any]] = None,  # More flexible type
        exception_handler: Optional[Callable[[Callable], Callable]] = None
    ) -> None:
        """
        Add a handler to the bot with optional event and exception handler.
        
        Args:
            handler: The async function to handle events
            event: Event pattern (string command or EventBuilder)
            exception_handler: Custom exception handler wrapper
        """
        
        if isinstance(event, str):
            event = events.NewMessage(pattern=event)
        elif event is None:
            event = events.NewMessage()
            
        if exception_handler is None:
            exception_handler = self.exception_handler
            
        async def wrapped_handler(event_obj) -> None:
            message = event_obj.message
            # Convert telegram message to our MessageModel for consistency
            message_model = MessageModel.from_telegram_message(message)
            self.message_manager.add_latest_message(message_model, True, True)
            await handler(event_obj)
            
            # print("stop propagation")
            # NOTE: ist es ok, wenn ich die propagation hier stoppe?
            raise events.StopPropagation
            
        wrapped_handler_with_exception = exception_handler(wrapped_handler)
        self.bot.add_event_handler(wrapped_handler_with_exception, event)
        
    
    def exception_handler(self, func: Callable[..., Any]) -> Callable[..., Any]:
        """
        Wrap a handler with exception handling logic.
        
        Args:
            func: The handler function to wrap
            
        Returns:
            Wrapped handler with exception handling
        """
        async def wrapper(event, *args, **kwargs) -> Any: 
            try: 
                sender_id = event.sender_id
                try:
                    return await func(event, *args, **kwargs)
                except exceptions.VerificationException as e:
                    log_telegram_api_error("user_verification", str(e), sender_id)
                    await self.message_manager.send_text(sender_id, e.message, True, True)
                    raise events.StopPropagation
                    # return False
                    
                except asyncio.TimeoutError as e:
                    log_telegram_api_error("conversation_timeout", str(e), sender_id)
                    await self.message_manager.send_text(sender_id, "Your request has expired. Please start again from the beginning.")
                    return False
                    
            except AttributeError as e:
                log_telegram_api_error("invalid_event", str(e))
            except asyncio.TimeoutError as e:
                log_telegram_api_error("timeout", str(e))
            except errors.rpcerrorlist.FloodWaitError as e:
                log_telegram_api_error("flood_wait", str(e))
            except errors.rpcerrorlist.UserIsBlockedError as e:
                log_telegram_api_error("user_blocked", str(e))
                
        return wrapper
    ### SECTION: Message Management - Moved to bot/message_manager.py
    
    # Message management methods have been moved to bot/message_manager.py for better separation of concerns.
    # Use self.message_manager directly for all message-related functionality.
    # Example: await self.message_manager.send_text(user_id, text)
    
    # NOTE: In theory, this is easier but does not work with consecutive lists
    # async def message_vanisher(self):
    #     while True:
    #         await asyncio.sleep(10)
            
    #         while len(self.latest_messages) > 3:
    #             self.delete_oldest_message()
            
    #         if len(self.latest_messages) == 0:
    #             continue
            
    #         i = 0
    #         while i < len(self.latest_messages):
    #             if isinstance(self.latest_messages[i], list):
    #                 while i > 0:
    #                     if (i < len(self.latest_messages) - 1):
    #                         self.delete_oldest_message()
    #                     i -= 1
    #                 break
    #             i += 1

    
    ### SECTION: Communication - Moved to bot/message_manager.py
    
    # Communication methods have been moved to bot/message_manager.py for better separation of concerns.
    # The delegation methods above provide backward compatibility.
    
    def keyboard_callback(self, user_id: int) -> events.CallbackQuery:
        """Get keyboard callback filter using KeyboardManager."""
        return KeyboardManager.get_keyboard_callback_filter(user_id)


    ### SECTION: Event Handlers - Moved to bot/commands.py

    # Most event handlers have been moved to bot/commands.py for better separation of concerns.
    # The handlers are registered in _register_handlers() method above.
    
        
    ### SECTION: Conversations - Moved to bot/conversations.py
    
    # Conversation management has been moved to bot/conversations.py for better separation of concerns.
    # The ConversationManager class handles multi-step conversation flows.
            
        
    ### SECTION: Data Management and Export - Moved to bot/telethon_models.py
    
    # Data management methods have been moved to their respective model classes:
    # - export_group_state() -> GroupState.export_state()
    # - import_group_state() -> GroupState.import_state()
    # - get_group_summary() -> GroupState.get_summary()
    # - get_timeout() -> BotConfiguration.get_timeout()
    # - get_conversation_timeout() -> BotConfiguration.get_conversation_timeout()
    # - validate_group_member() -> Use GroupMember() constructor directly
    # - create_message_model() -> Use MessageModel.from_telegram_message() directly
    # - with_timeout() -> Moved to utils/decorators.py
    