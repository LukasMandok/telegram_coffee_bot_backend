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
Note: Telethon keyboard helpers live in bot/message_flow_helpers.py.
Note: Message management functionality has been moved to bot/message_manager.py (MessageManager).
Note: Conversation management has been moved to bot/conversations.py (ConversationManager).
Note: Telegram-specific models have been moved to bot/telethon_models.py.
"""

import asyncio
import re
from typing import Callable, Optional, Dict, Union, Any, TYPE_CHECKING, cast

# Runtime imports - actually used at runtime
from telethon import TelegramClient, events, errors
from telethon.tl.functions.bots import SetBotCommandsRequest, SetBotMenuButtonRequest
from telethon.tl.types import (
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeUsers,
    BotCommandScopeChats,
    BotCommandScopePeerUser,
    BotMenuButtonCommands,
    InputUserEmpty,
    InputPeerUser,
    InputUser,
)

from ..handlers import exceptions, users
from ..bot.telethon_models import ( MessageModel, BotConfiguration )
from ..bot.message_flow_helpers import get_keyboard_callback_filter
from ..common.log import Logger

from ..bot.message_manager import MessageManager
from ..bot.commands import CommandManager
from ..bot.conversations import ConversationManager
from ..bot.group_keyboard_manager import GroupKeyboardManager
from ..bot.session_manager import SessionManager
from ..bot.coffee_card_manager import CoffeeCardManager
from ..bot.debt_manager import DebtManager
from ..bot.command_catalog import BOT_COMMANDS
from ..database.snapshot_manager import SnapshotManager
from ..database.base_repo import BaseRepository

# Type-only imports - only needed for type annotations
if TYPE_CHECKING:
    pass

"""Command list is defined in bot.command_catalog.BOT_COMMANDS."""



### API Handler

class TelethonAPI:
    """
    Main Telegram bot API handler.

    This class manages bot initialization, message handling, user conversations,
    and coffee group ordering through Telegram inline keyboards.
    """
    
    def __init__(self, api_id: Union[int, str], api_hash: str, bot_token: str, repo: BaseRepository) -> None:
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

        self.repo = repo
        self.logger = Logger("TelethonAPI")

        session_name = "coffee_bot_session"

        self.bot: TelegramClient = TelegramClient(
            session_name,
            self.config.api_id,
            self.config.api_hash,
        )

        self.message_manager = MessageManager(self.bot)
        self.conversation_manager = ConversationManager(self)
        self.command_manager = CommandManager(self)
        self.group_keyboard_manager = GroupKeyboardManager(self)
        self.session_manager = SessionManager(self)
        self.debt_manager = DebtManager(self)
        self.coffee_card_manager = CoffeeCardManager(self)

        # Register all handlers
        self._register_handlers()

    def get_snapshot_manager(self) -> SnapshotManager:
        """Get the SnapshotManager instance from the repository."""
        snapshot_manager = getattr(self.repo, "snapshot_manager", None)
        if snapshot_manager is None:
            raise RuntimeError("SnapshotManager is not initialized yet; call repository.connect() first")
        return snapshot_manager
        
    def _register_handlers(self) -> None:
        """Register all bot event handlers."""
        # Use CommandManager methods directly for cleaner architecture
        self.add_handler(lambda event: self.command_manager.handle_start_command(event), '/start')
        self.add_handler(lambda event: self.command_manager.handle_order_command(event), '/order')
        self.add_handler(lambda event: self.command_manager.handle_password_command(event), '/password')
        self.add_handler(lambda event: self.command_manager.handle_card_command(event), "/cards")
        self.add_handler(lambda event: self.command_manager.handle_new_card_command(event), "/new_card")
        self.add_handler(lambda event: self.command_manager.handle_close_card_command(event), "/close_card")
        self.add_handler(lambda event: self.command_manager.handle_paypal_command(event), "/paypal")
        self.add_handler(lambda event: self.command_manager.handle_debt_command(event), "/debt")
        self.add_handler(lambda event: self.command_manager.handle_credit_command(event), "/credit")
        self.add_handler(lambda event: self.command_manager.handle_settings_command(event), "/settings")
        self.add_handler(lambda event: self.command_manager.handle_feedback_command(event), "/feedback")
        self.add_handler(lambda event: self.command_manager.handle_sync_command(event), "/sync")
        self.add_handler(lambda event: self.command_manager.handle_snapshots_command(event), "/snapshots")
        self.add_handler(lambda event: self.command_manager.handle_users_command(event), "/users")
        self.add_handler(lambda event: self.command_manager.handle_help_command(event), "/help")
        self.add_handler(lambda event: self.command_manager.handle_cancel_command(event), "/cancel")
        # TODO: check if I actually need them
        self.add_handler(lambda event: self.command_manager.handle_complete_session_command(event), "/complete_session")
        self.add_handler(lambda event: self.command_manager.handle_cancel_session_command(event), "/cancel_session")
        
        # Handle persistent keyboard button presses
        self.add_handler(lambda event: self.command_manager.handle_persistent_button(event), events.NewMessage(incoming=True, pattern=re.compile(r'^(Place Order|Show Debts)$')))
        
        # Only trigger on messages that are purely a number (quick-order shortcut)
        self.add_handler(lambda event: self.command_manager.handle_digits_command(event), events.NewMessage(incoming=True, pattern=re.compile(r'^\d+$')))
        self.add_handler(lambda event: self.command_manager.handle_unknown_command(event))

        async def _touch_callback_activity(event_obj: events.CallbackQuery.Event) -> None:
            sender_id_raw = event_obj.sender_id
            if sender_id_raw is None:
                return

            sender_id = int(sender_id_raw)
            if self.conversation_manager.has_active_conversation(sender_id):
                cb_id = getattr(event_obj, "id", None)
                event_key = f"cb:{cb_id}" if cb_id is not None else None
                self.conversation_manager.touch_conversation(
                    sender_id,
                    reason="callback_seen",
                    event_key=event_key,
                )

        # Treat any callback press during an active conversation as activity.
        # Do not stop propagation; real callback handlers can still run.
        self.bot.add_event_handler(_touch_callback_activity, events.CallbackQuery())

        # Callback-query handlers (registered directly; do NOT use add_handler wrapper).
        self.bot.add_event_handler(
            self.exception_handler(self.command_manager.handle_debt_quick_confirm_callback),
            events.CallbackQuery(func=lambda e: (e.data or b"").startswith(b"debt_quick_confirm:")),
        )
    
    async def setup_bot_commands(self) -> None:
        """
        Set up the bot's command list using Telegram's setMyCommands API.
        
        This configures the commands that appear in the Telegram UI when users
        type '/' in the chat with the bot.
        """
        admin_commands = [BotCommand(command=cmd, description=desc) for cmd, desc, _ in BOT_COMMANDS]
        user_commands = [
            BotCommand(command=cmd, description=desc)
            for cmd, desc, show_for_normal_users in BOT_COMMANDS
            if show_for_normal_users
        ]
            
        scopes = [
            BotCommandScopeDefault(),
            BotCommandScopeUsers(),
            BotCommandScopeChats(),
        ]

        # Telegram chooses commands by (scope, lang_code). To avoid stale BotFather WebApp
        # commands sticking around, we set a few common language codes too.
        lang_codes = ["", "en", "de"]

        try:
            # 1) Default commands for normal users (applies to everyone)
            for scope in scopes:
                for lang_code in lang_codes:
                    await self.bot(
                        SetBotCommandsRequest(
                            scope=scope,
                            lang_code=lang_code,
                            commands=user_commands,
                        )
                    )

            # 2) Override commands for admins (user-specific)
            admin_user_ids = await self.repo.get_registered_admins()
            for admin_user_id in admin_user_ids:
                try:
                    peer = await self.bot.get_input_entity(admin_user_id)
                    if not isinstance(peer, InputPeerUser):
                        continue
                    input_user = InputUser(user_id=peer.user_id, access_hash=peer.access_hash)
                    admin_scope = BotCommandScopePeerUser(peer=peer, user_id=input_user)
                    for lang_code in lang_codes:
                        await self.bot(
                            SetBotCommandsRequest(
                                scope=admin_scope,
                                lang_code=lang_code,
                                commands=admin_commands,
                            )
                        )
                except Exception:
                    continue

            # Try to reset the menu button to Telegram's default.
            # Telegram docs: BotMenuButtonDefault has no effect for "all users" scope.
            # Use BotMenuButtonCommands to force-enable the commands menu button.
            try:
                await self.bot(
                    SetBotMenuButtonRequest(
                        user_id=InputUserEmpty(),
                        button=BotMenuButtonCommands(),
                    )
                )
            except Exception:
                pass

            self.logger.info(
                f"[BOT] Bot commands configured successfully (user_commands={len(user_commands)}, admin_commands={len(admin_commands)}, admins={len(admin_user_ids)})"
            )
        except Exception as e:
            self.logger.error("Failed to configure bot commands", exc=e)
    
    async def run(self) -> None:
        """
        Start the message vanisher task and run the bot until disconnected.
        
        This method starts background tasks and keeps the bot running
        until manually disconnected or an error occurs.
        """
        self.get_snapshot_manager().set_api(self)

        # Telethon's type stubs don't always mark `start()` as awaitable,
        # but at runtime it must be awaited in an async app.
        await cast(Any, self.bot).start(bot_token=self.config.bot_token)
        self.logger.info(f"Telegram bot started successfully with API ID: {self.config.api_id}")

        # Sessions should only live while the bot runs. On startup, cancel any leftover
        # active sessions from previous runs (this also deletes cancelled sessions with no orders).
        await self.session_manager.cancel_leftover_active_sessions_on_startup()

        # Set up bot commands in Telegram UI
        await self.setup_bot_commands()
        
        asyncio.create_task(self.message_manager.message_vanisher()) 
        asyncio.create_task(self.coffee_card_manager.load_from_db())

        # Telethon's type stubs sometimes model run_until_disconnected() as returning None,
        # but waiting on the disconnected Future is equivalent here.
        await self.bot.disconnected
        
        
    ### SECTION: handler administration
    
    def add_handler(
        self,
        handler: Callable[..., Any],
        event: Optional[Union[str, Any]] = None,  # More flexible type
        exception_handler: Optional[Callable[[Callable], Callable]] = None,
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
            sender_id = event_obj.sender_id

            # Convert telegram message to our MessageModel for consistency
            message_model = MessageModel.from_telegram_message(message)
            
            # Check if there's an active conversation for this user
            has_active_conversation = self.conversation_manager.has_conversation(sender_id)

            if has_active_conversation:
                msg_id = getattr(event_obj.message, "id", None)
                event_key = f"msg:{msg_id}" if msg_id is not None else None
                self.conversation_manager.touch_conversation(
                    sender_id,
                    reason="message_seen",
                    event_key=event_key,
                )
            
            # Only create a new conversation group if there's no active conversation
            # Otherwise, add to the existing conversation
            new_conversation = not has_active_conversation
            self.message_manager.add_latest_message(sender_id, message_model, conv=True, new=new_conversation)

            # If the user is currently in a conversation, treat anything that looks
            # like a Telegram command as plain input. Only /cancel stays active.
            if has_active_conversation:
                raw_text = (event_obj.message.message or "").strip()
                if raw_text.startswith("/") and raw_text.lower() != "/cancel":
                    return

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
                    self.logger.warning(
                        f"[TELEGRAM] user_verification failed for user_id={sender_id}: {e}",
                        exc=e,
                    )
                    await self.message_manager.send_text(sender_id, e.message, True, True)
                    raise events.StopPropagation
                    # return False
                    
                except asyncio.TimeoutError as e:
                    self.logger.debug(f"[TELEGRAM] conversation_timeout for user_id={sender_id}")
                    await self.conversation_manager.handle_timeout_abort(
                        sender_id,
                        "telethon_handler",
                        clear_latest_messages=True,
                    )
                    raise events.StopPropagation
                    
            except AttributeError as e:
                self.logger.error(f"[TELEGRAM] invalid_event: {e}", exc=e)
                # Prevent other handlers from handling this same event after an internal error
                raise events.StopPropagation
            # except events.StopPropagation:
            #     raise
            except asyncio.TimeoutError as e:
                self.logger.debug(f"[TELEGRAM] handler timeout: {e}", exc=e)
            except errors.rpcerrorlist.FloodWaitError as e:
                self.logger.warning(f"[TELEGRAM] flood_wait: {e}", exc=e)
            except errors.rpcerrorlist.UserIsBlockedError as e:
                self.logger.info(f"[TELEGRAM] user_blocked: {e}", exc=e)
                
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
        """Get callback filter for inline keyboard clicks from this user."""
        return get_keyboard_callback_filter(user_id)


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
    