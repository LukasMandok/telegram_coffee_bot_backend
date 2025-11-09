"""
Telegram bot commands and Telegram-specific business logic.

This module contains command handlers and orchestration functions that combine
Telegram-specific operations with domain business logic.
"""

from typing import Dict, Any, List, TYPE_CHECKING
from telethon import events
from ..models.beanie_models import TelegramUser
from ..models.coffee_models import CoffeeCard
from .conversations import ConversationManager
from .keyboards import KeyboardManager
from ..handlers import users
from ..dependencies import dependencies as dep
from ..exceptions.coffee_exceptions import (
    InvalidCoffeeCountError, 
    InsufficientCoffeeError, 
    SessionNotActiveError,
    UserNotFoundError
)
from ..common.log import (
    log_telegram_command, log_telegram_callback, log_coffee_session_started,
    log_coffee_session_cancelled, log_unexpected_error, log_user_login_success,
    log_user_login_failed, Logger
)

if TYPE_CHECKING:
    from ..api.telethon_api import TelethonAPI


# Command Handlers - These handle Telegram events and orchestrate business logic

class CommandManager:
    """
    Manages all command handling for the Telegram bot.
    
    This class provides a centralized way to handle all bot commands
    with proper dependency injection and state management.
    """
    
    def __init__(self, api: "TelethonAPI"):
        """
        Initialize the command manager.
        
        Args:
            api: The TelethonAPI instance for bot communication
        """
        self.api = api
        self.logger = Logger("CommandManager")
        
    async def _check_and_notify_active_conversation(self, user_id: int) -> bool:
        """
        Check if user has an active conversation and notify them if they do.
        
        Args:
            user_id: The Telegram user ID to check
            
        Returns:
            bool: True if user has an active conversation (command should abort),
                  False if user is free to start a new conversation
        """
        if self.api.conversation_manager.has_active_conversation(user_id):
            await self.api.message_manager.send_text(
                user_id,
                "❌ You already have an active conversation. Please finish it first or use /cancel.",
                True,
                True
            )
            return True
        return False
    
    async def handle_start_command(self, event: events.NewMessage.Event) -> None:
        """
        Handle the /start command for user registration.
        
        Args:
            event: The NewMessage event containing /start command
        """
        user_id = event.sender_id
        log_telegram_command(user_id, "/start", getattr(event, 'chat_id', None))

        # Check if user is already registered
        if await users.check_user(user_id):
            await self.api.message_manager.send_keyboard(
                user_id, 
                "There is nothing more to do. You are already registered.", 
                KeyboardManager.get_persistent_keyboard(),
                True
            )
            return 

        # Use conversation manager for registration
        await self.api.conversation_manager.register_conversation(user_id)

    async def handle_cancel_command(self, event: events.NewMessage.Event) -> None:
        """
        Handle the /cancel command to interrupt active conversations.
        
        Args:
            event: The NewMessage event containing /cancel command
        """
        user_id = event.sender_id
        log_telegram_command(user_id, "/cancel", getattr(event, 'chat_id', None))
        
        # Check if user has an active conversation
        if self.api.conversation_manager.has_active_conversation(user_id):
            conversation_cancelled = self.api.conversation_manager.cancel_conversation(user_id)
            if conversation_cancelled:
                await self.api.message_manager.send_text(
                    user_id,
                    "❌ Current conversation cancelled. You can start over anytime.",
                    True,
                    True
                )
            else:
                await self.api.message_manager.send_text(
                    user_id,
                    "❌ Failed to cancel conversation. Please try again.",
                    True,
                    True
                )
        else:
            await self.api.message_manager.send_text(
                user_id,
                "ℹ️ No active conversation to cancel.",
                True,
                True
            )

    async def handle_persistent_button(self, event: events.NewMessage.Event) -> None:
        """
        Handle persistent keyboard button presses.
        
        Args:
            event: The NewMessage event containing button text
        """
        user_id = event.sender_id
        button_text = event.message.message
        log_telegram_command(user_id, f"button:{button_text}", getattr(event, 'chat_id', None))
        
        # Map button text to commands
        if button_text == "Place Order":
            await self.handle_order_command(event)
        elif button_text == "Show Debts":
            await self.handle_debt_command(event)


    async def handle_password_command(self, event: events.NewMessage.Event) -> None:
        """
        Test password handler for /password command.
        
        Args:
            event: The NewMessage event containing /password command
        """
        # get the message and cut off the /password part
        message = event.message.message
        user_id = event.sender_id
        log_telegram_command(user_id, "/password", getattr(event, 'chat_id', None))
        
        try:
            password = message.split(" ")[1]
            
            password_correct = await users.check_password(password)
            if password_correct:
                log_user_login_success(user_id)
            else:
                log_user_login_failed(user_id, "incorrect_password")
            
        # TODO: improve exceptions
        except Exception as e:
            log_user_login_failed(user_id, f"password_not_provided: {str(e)}")

    @dep.verify_user
    async def handle_user_verification_command(self, event: events.NewMessage.Event) -> None:
        """
        Handler to verify if a user is registered.
        
        Args:
            event: The NewMessage event from user verification test
        """
        user_id = event.sender_id
        await self.api.message_manager.send_text(user_id, "You are a registered user.", True, True)

    @dep.verify_admin    
    async def handle_admin_verification_command(self, event: events.NewMessage.Event) -> None:
        """
        Handler to verify if a user is an admin.
        
        Args:
            event: The NewMessage event from admin verification test
        """
        user_id = event.sender_id
        await self.api.message_manager.send_text(user_id, "You are a registered admin.", True, True)

    @dep.verify_admin
    async def handle_add_passive_user_command(self, event: events.NewMessage.Event) -> None:
        """
        Admin-only command to add a new passive user.
        
        Args:
            event: The NewMessage event containing /add_user command
        """
        user_id = event.sender_id
        log_telegram_command(user_id, "/add_user", getattr(event, 'chat_id', None))
        
        # # Check if user already has an active conversation
        # if self.api.conversation_manager.has_active_conversation(user_id):
        #     await self.api.message_manager.send_text(
        #         user_id,
        #         "❌ You already have an active conversation. Please finish it first or use /cancel.",
        #         True,
        #         True
        #     )
        #     return
        
        # Start the conversation for adding passive users
        await self.api.conversation_manager.add_passive_user_conversation(user_id)

    @dep.verify_user
    async def handle_order_command(self, event: events.NewMessage.Event) -> None:
        """
        Handle the /order command to start coffee ordering.
        
        Args:
            event: The NewMessage event containing /order command
        """
        user_id = event.sender_id
        log_telegram_command(user_id, "/order", getattr(event, 'chat_id', None))

        # Check if user already has an active conversation
        if await self._check_and_notify_active_conversation(user_id):
            return
        
        # Start the group selection conversation
        await self.api.conversation_manager.group_selection(user_id)

    @dep.verify_user
    async def handle_new_card_command(self, event: events.NewMessage.Event) -> None:
        """
        Handle the /new_card command to create a new coffee card.
        
        Args:
            event: The NewMessage event containing /new_card command
        """
        user_id = event.sender_id
        log_telegram_command(user_id, "/new_card", getattr(event, 'chat_id', None))

        # Check if user already has an active conversation
        if await self._check_and_notify_active_conversation(user_id):
            return
        
        # Start the coffee card creation conversation
        await self.api.conversation_manager.create_coffee_card_conversation(user_id)

    @dep.verify_user
    async def handle_paypal_command(self, event: events.NewMessage.Event) -> None:
        """
        Handle the /paypal command to set up or change PayPal link.
        
        Args:
            event: The NewMessage event containing /paypal command
        """
        user_id = event.sender_id
        log_telegram_command(user_id, "/paypal", getattr(event, 'chat_id', None))

        # Check if user already has an active conversation
        if await self._check_and_notify_active_conversation(user_id):
            return
        
        # Get the user (registered Telegram users manage PayPal links)
        user = await dep.get_repo().find_user_by_id(user_id)
        
        # Call setup as a standalone conversation (not a sub-conversation)
        # Don't pass existing_conv so the decorator properly cleans up state
        setup_success = await self.api.conversation_manager.setup_paypal_link_subconversation(
            user_id, user=user, show_current=True
        )
        
        if not setup_success:
            await self.api.message_manager.send_text(
                user_id, 
                "❌ PayPal setup was not completed.\n"
                "You can try again anytime with `/paypal`", 
                True, True
            )

    async def handle_digits_command(self, event: events.NewMessage.Event) -> None:
        """
        Handle digit messages (temporary handler for testing).
        
        Skips processing if user has an active conversation to avoid
        interfering with conversation inputs like coffee counts or prices.
        
        Args:
            event: The NewMessage event containing digits
        """
        user_id = event.sender_id
        
        # Don't process digits if user is in an active conversation
        if self.api.conversation_manager.has_active_conversation(user_id):
            return
        
        # TODO: This handler can be used for quick coffee ordering via digit shortcuts
        await self.api.message_manager.send_text(user_id, f'catches digits: {event.text}', True, True)

    async def handle_unknown_command(self, event: events.NewMessage.Event) -> None:
        """
        Handle unknown commands sent to the bot.
        
        Only processes messages that clearly look like commands (start with /).
        This prevents interference with password inputs and other conversation flows.
        
        Args:
            event: The NewMessage event containing an unknown command
        """
        sender_id = event.sender_id
        message = event.message.message.strip()
        
        # Only process messages that start with / (actual commands)
        # This prevents interference with passwords and conversation inputs
        # if not message.startswith('/'):
        #     return  # Not a command, don't process
        
        if self.api.conversation_manager.has_active_conversation(sender_id):
            self.logger.debug(f"Command ignored due to active conversation: {message}")
            return
        
        self.logger.debug(f"Processing unknown command: {message}")
        active_conversations = self.api.conversation_manager.get_active_conversations()
        self.logger.debug(f"Active conversation: {active_conversations}")
        
        await self.api.message_manager.send_text(sender_id, f"**{message}** is an unknown command.", True, True)

    @dep.verify_user
    async def handle_complete_session_command(self, event: events.NewMessage.Event) -> None:
        """
        Handle the /complete_session command to finalize a coffee session.
        
        Args:
            event: The NewMessage event containing /complete_session command
        """
        user_id = event.sender_id
        log_telegram_command(user_id, "/complete_session", getattr(event, 'chat_id', None))
        
        try:
            # session = await get_user_active_session(user_id)
            session = await self.api.session_manager.get_active_session()
            
            if session is None:
                await self.api.message_manager.send_text(
                    user_id,
                    "❌ You don't have an active coffee session.",
                    True, True
                )
                return
            
            completed = await self.api.session_manager.complete_session(user_id)
            # Complete the session (store user_id before completing)
            if not completed:
                await self.api.message_manager.send_text(
                    user_id,
                    "❌ No active session to complete.",
                    True, True
                )
                return
                        
            # Notify all participants who have made orders
            for name, group_member in session.group_state.members.items():
                # todo: also check, that they were not part of the session (as a participant)
                if group_member.coffee_count > 0:
                    # Skip PassiveUsers (who don't have user_id)
                    if group_member.user_id is None:
                        continue
                    
                    await session.fetch_link("initiator")
                    initiator_display_name = session.initiator.display_name # type: ignore
                    
                    await self.api.message_manager.send_text(
                        group_member.user_id,
                        f"{initiator_display_name} has ordered {group_member.coffee_count} coffees for you.\n",
                        True, True
                    )
            
            # # Also notify the initiator if they haven't made an order
            # if initiator_user_id not in notified_users:
            #     await self.api.message_manager.send_text(
            #         initiator_user_id,
            #         f"✅ **Session Completed!**\n"
            #         f"Total: {total_coffees} coffees\n"
            #         f"Session ID: `{completed_session.id}`",
            #         True, True
            #     )
                
        except Exception as e:
            await self.api.message_manager.send_text(
                user_id,
                f"❌ Failed to complete session: {str(e)}",
                True, True
            )

    @dep.verify_user
    async def handle_cancel_session_command(self, event: events.NewMessage.Event) -> None:
        """
        Handle the /cancel_session command to cancel a coffee session.
        
        Args:
            event: The NewMessage event containing /cancel_session command
        """
        user_id = event.sender_id
        log_telegram_command(user_id, "/cancel_session", getattr(event, 'chat_id', None))
        
        try:            
            session = await self.api.session_manager.get_user_active_session(user_id)
            
            if session is None:
                await self.api.message_manager.send_text(
                    user_id,
                    "❌ You don't have an active coffee session.",
                    True, True
                )
                return
            
            # Cancel the session
            await self.api.session_manager.cancel_session()
            
            # Notify the user who cancelled

            await self.api.message_manager.send_text(
                user_id,
                f"❌ **Session Cancelled**\n"
                f"Coffee session `{session.id}` has been cancelled.",
                True, True
            )
            
            # Also notify the user who cancelled (in case they have no orders)
            if user_id not in session.coffee_counts:
                await self.api.message_manager.send_text(
                    user_id,
                    f"❌ **Session Cancelled**\n"
                    f"Coffee session `{session.id}` has been cancelled.",
                    True, True
                )
                
        except Exception as e:
            await self.api.message_manager.send_text(
                user_id,
                f"❌ Failed to cancel session: {str(e)}",
                True, True
            )

    @dep.verify_user
    async def handle_close_card_command(self, event: events.NewMessage.Event) -> None:
        """
        Handle the /close_card command to manually complete the oldest coffee card.
        
        Args:
            event: The NewMessage event containing /close_card command
        """
        user_id = event.sender_id
        log_telegram_command(user_id, "/close_card", getattr(event, 'chat_id', None))
        
        # Check if user already has an active conversation
        if await self._check_and_notify_active_conversation(user_id):
            return
        
        try:
            # Get all active coffee cards from the manager (already loaded)
            cards = self.api.coffee_card_manager.cards
            self.logger.trace(f"Active cards: {len(cards)}")
            
            
            if not cards:
                oldest_card = None
            else:
                # Sort by created_at to get oldest first
                sorted_cards = sorted(cards, key=lambda c: c.created_at)
                oldest_card = sorted_cards[0]
            
            if not oldest_card:
                await self.api.message_manager.send_text(
                    user_id,
                    "❌ No active coffee cards found.",
                    True, True
                )
                return
            
            # Start the completion conversation (handles confirmation if needed)
            success = await self.api.conversation_manager.close_card_conversation(
                user_id,
                card=oldest_card
            )
            
            if not success:
                # User cancelled - conversation already handled the message
                return
            
        except ValueError as e:
            await self.api.message_manager.send_text(
                user_id,
                f"❌ Error: {str(e)}",
                True, True
            )
        except Exception as e:
            import traceback
            self.logger.error(f"Unexpected error in handle_close_card_command for user {user_id}", exc=e)
            self.logger.debug(f"Full traceback:\n{traceback.format_exc()}")
            await self.api.message_manager.send_text(
                user_id,
                f"❌ Failed to complete coffee card: {str(e)}",
                True, True
            )

    @dep.verify_user
    async def handle_debt_command(self, event: events.NewMessage.Event) -> None:
        """
        Handle the /debt command to show debt overview.
        
        Args:
            event: The NewMessage event containing /debt command
        """
        user_id = event.sender_id
        log_telegram_command(user_id, "/debt", getattr(event, 'chat_id', None))
        
        # Check if user already has an active conversation
        if await self._check_and_notify_active_conversation(user_id):
            return
        
        # Start the debt overview conversation
        await self.api.conversation_manager.debt_overview_conversation(user_id)

    @dep.verify_user
    async def handle_credit_command(self, event: events.NewMessage.Event) -> None:
        """
        Handle the /credit command to show credit overview (money owed to the user).
        
        Args:
            event: The NewMessage event containing /credit command
        """
        user_id = event.sender_id
        log_telegram_command(user_id, "/credit", getattr(event, 'chat_id', None))
        
        # Check if user already has an active conversation
        if await self._check_and_notify_active_conversation(user_id):
            return
        
        # Start the credit overview conversation
        await self.api.conversation_manager.credit_overview_conversation(user_id)

    @dep.verify_user
    async def handle_settings_command(self, event: events.NewMessage.Event) -> None:
        """
        Handle the /settings command to adjust user preferences.
        
        Args:
            event: The NewMessage event containing /settings command
        """
        user_id = event.sender_id
        log_telegram_command(user_id, "/settings", getattr(event, 'chat_id', None))
        
        # Check if user already has an active conversation
        if await self._check_and_notify_active_conversation(user_id):
            return
        
        # Start the settings conversation
        await self.api.conversation_manager.settings_conversation(user_id)

    async def handle_help_command(self, event: events.NewMessage.Event) -> None:
        """
        Handle the /help command to show available commands and their descriptions.
        
        Args:
            event: The NewMessage event containing /help command
        """
        user_id = event.sender_id
        log_telegram_command(user_id, "/help", getattr(event, 'chat_id', None))
        
        help_text = (
            "🤖 **Coffee Bot Commands**\n\n"
            "**Getting Started:**\n"
            "• `/start` - Register with the bot\n\n"
            
            "**Coffee Orders:**\n"
            "• `/order` - Create or join a session to place an order\n"
            "• `/cancel` - Cancel the current conversation\n\n"
            
            "**Coffee Cards:**\n"
            "• `/card` - Show status and manage all coffee cards\n"
            "• `/new_card` - Create a new coffee card that you paid for\n"
            "• `/close_card` - Close the last active coffee card\n\n"
            
            "**Finances:**\n"
            "• `/debt` - Show and manage your debts\n"
            "• `/credit` - Display and manage debts others owe to you\n"
            "• `/paypal` - Setup your paypal.me link\n\n"
            
            "**Settings:**\n"
            "• `/settings` - Adjust your personal preferences\n"
            "  📋 **Ordering:** Page size, group sorting\n"
            "  💬 **Vanishing Messages:** Auto-cleanup, threshold\n"
            "  🔧 **Administration (Admins Only):**\n"
            "     📊 Logging settings\n"
            "     🔔 Notification preferences\n"
            "  🔧 **Administration:** Admin features (coming soon)\n\n"
            
            "**Other:**\n"
            "• `/help` - Show this help message\n\n"
            
            "💡 **Tip:** Most commands use interactive inline keyboards for easy navigation!"
        )
        
        await self.api.message_manager.send_text(
            user_id,
            help_text,
            vanish=True,
            conv=True,
            link_preview=False
        )

