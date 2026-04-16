"""Telegram bot commands and Telegram-specific business logic."""

import difflib
import traceback
from typing import TYPE_CHECKING

from telethon import events

from .command_catalog import BOT_COMMANDS, get_all_commands, get_user_commands
from .keyboards import KeyboardManager
from ..handlers import users
from ..dependencies import dependencies as dep
from ..common.log import (
    log_telegram_command,
    log_user_login_success,
    log_user_login_failed,
    Logger,
)

from ..services.gsheet_sync import sync_all_cards_once

from ..models.coffee_models import UserDebt, PaymentReason, CoffeeCard
from ..models.beanie_models import TelegramUser, PassiveUser
from .message_flow_ids import DebtQuickConfirmCallbacks

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

    async def handle_debt_quick_confirm_callback(self, event: events.CallbackQuery.Event) -> None:
        """Handle callback buttons for the debtor 'Did you already pay?' message."""
        auto_delete_seconds = 5
        eps = 1e-9

        sender_id = event.sender_id
        if not isinstance(sender_id, int):
            self.logger.warning(f"Debt quick-confirm: invalid sender_id={sender_id!r}")
            return
        data = (event.data or b"").decode("utf-8", errors="ignore")
        if not data.startswith(DebtQuickConfirmCallbacks.PREFIX):
            return

        try:
            await event.answer()
        except Exception:
            pass

        try:
            await event.delete()
        except Exception:
            pass

        if data.startswith(DebtQuickConfirmCallbacks.NO_PREFIX):
            await self.api.message_manager.send_temp_notification(
                sender_id,
                "You can view and mark your debts as paid later using /debt",
                auto_delete=auto_delete_seconds,
                silent=True,
                vanish=False,
                conv=False,
            )
            raise events.StopPropagation

        if not data.startswith(DebtQuickConfirmCallbacks.YES_PREFIX):
            return

        debt_id = data[len(DebtQuickConfirmCallbacks.YES_PREFIX) :]
        card_name = "this card"
        paid_amount = 0.0

        try:
            debt = await UserDebt.get(debt_id)
            if debt is None:
                self.logger.warning(f"Debt quick-confirm: debt not found (debt_id={debt_id})")
            else:
                for link_name in ("coffee_card", "creditor", "debtor"):
                    try:
                        await debt.fetch_link(link_name)
                    except Exception:
                        pass

                if isinstance(debt.coffee_card, CoffeeCard):
                    card_name = debt.coffee_card.name

                outstanding = max(0.0, float(debt.total_amount) - float(debt.paid_amount))
                if outstanding > eps:
                    paid_amount = outstanding
                    snapshot_manager = self.api.get_snapshot_manager()
                    await snapshot_manager.create_snapshot(
                        reason="Apply Payment (debtor quick confirm)",
                        context="apply_payment_debtor_quick_confirm",
                        collections=("user_debts", "payments"),
                        save_in_background=True,
                    )

                    await self.api.debt_manager._apply_payment_to_debt(
                        debt,
                        outstanding,
                        reason=PaymentReason.DEBTOR_MARKED_PAID,
                    )

                    await self._notify_creditor_debt_quick_confirm(
                        debt=debt,
                        paid_amount=paid_amount,
                        card_name=card_name,
                    )
        except Exception as exc:
            self.logger.warning("Debt quick-confirm failed", exc=exc)

        await self.api.message_manager.send_temp_notification(
            sender_id,
            f"✅ Your debts for {card_name} are marked as paid.",
            auto_delete=auto_delete_seconds,
            silent=True,
            vanish=False,
            conv=False,
        )

        raise events.StopPropagation


    async def _notify_creditor_debt_quick_confirm(
        self,
        *,
        debt: UserDebt,
        paid_amount: float,
        card_name: str,
    ) -> None:
        if paid_amount <= 1e-9:
            return

        if not isinstance(debt.creditor, TelegramUser):
            return
        if not isinstance(debt.debtor, PassiveUser):
            return

        creditor_user_id = debt.creditor.user_id
        debtor_name = debt.debtor.display_name or "This user"

        try:
            debtor_debts = await self.api.debt_manager.get_user_debts(
                debt.debtor,
                include_settled=False,
            )

            remaining_total = sum(
                max(0.0, float(d.total_amount) - float(d.paid_amount))
                for d in debtor_debts
                if isinstance(d.creditor, TelegramUser) and d.creditor.user_id == creditor_user_id
            )

            remaining_line = (
                f"✅ {debtor_name} doesn't owe you any more money."
                if remaining_total <= 1e-9
                else f"💰 Remaining total owed by {debtor_name}: **{remaining_total:.2f} €**."
            )

            await self.api.message_manager.send_user_notification(
                creditor_user_id,
                (
                    "💸 **Payment update**\n\n"
                    f"{debtor_name} marked **{paid_amount:.2f} €** as paid.\n"
                    f"Card: **{card_name}**\n\n"
                    f"{remaining_line}"
                ),
            )
        except Exception as exc:
            self.logger.warning("Debt quick-confirm: creditor notify failed", exc=exc)


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

    @dep.verify_admin
    async def handle_sync_command(self, event: events.NewMessage.Event) -> None:
        """Admin command to trigger a one-shot export to Google Sheets."""
        user_id = event.sender_id
        log_telegram_command(user_id, "/sync", getattr(event, 'chat_id', None))

        await self.api.message_manager.send_text(
            user_id,
            "📄 Starting Google Sheets sync…",
            True,
            True,
        )

        try:
            await sync_all_cards_once()
        except Exception as exc:
            await self.api.message_manager.send_text(
                user_id,
                f"❌ Google Sheets sync failed: {type(exc).__name__}: {exc!r}",
                True,
                True,
            )
            return

        await self.api.message_manager.send_text(
            user_id,
            "✅ Google Sheets sync finished.",
            True,
            True,
        )

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
    async def handle_card_command(self, event: events.NewMessage.Event) -> None:
        """Handle the /cards command to show the coffee card menu."""
        user_id = event.sender_id
        log_telegram_command(user_id, "/cards", getattr(event, 'chat_id', None))

        # Check if user already has an active conversation
        if await self._check_and_notify_active_conversation(user_id):
            return

        await self.api.conversation_manager.card_menu_conversation(user_id)

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
        
        # Start the new PayPal setup conversation using MessageFlow
        setup_success = await self.api.conversation_manager.paypal_setup_conversation(user_id)
        
        if not setup_success:
            await self.api.message_manager.send_text(
                user_id, 
                "❌ PayPal setup was not completed.\n"
                "You can try again anytime with `/paypal`", 
                True, True
            )

    @dep.verify_admin
    async def handle_snapshots_command(self, event: events.NewMessage.Event) -> None:
        """Admin command to manage snapshots via a MessageFlow conversation."""
        user_id = event.sender_id
        log_telegram_command(user_id, "/snapshots", getattr(event, 'chat_id', None))

        if await self._check_and_notify_active_conversation(user_id):
            return

        await self.api.conversation_manager.snapshots_conversation(user_id)

    @dep.verify_user
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

        raw = (event.text or "").strip()
        try:
            quantity = int(raw)
        except ValueError:
            return

        await self.api.conversation_manager.quick_order_conversation(user_id, quantity)

    async def handle_unknown_command(self, event: events.NewMessage.Event) -> None:
        """Handle unknown commands and suggest close matches.

        - If the user writes /something, suggest similar commands.
        - If the user writes a single word like order (no leading /), treat it like /order.
        - Avoid reacting to normal chat: multi-word messages without / are ignored.
        """
        sender_id = event.sender_id
        message = (event.message.message or "").strip()

        if not message:
            return

        if self.api.conversation_manager.has_active_conversation(sender_id):
            return

        if message == "/":
            await self.handle_help_command(event)
            return

        token = message.split(maxsplit=1)[0]
        has_slash = token.startswith("/")

        # If the user forgot the leading '/', only handle single-word inputs.
        if not has_slash and len(message.split()) != 1:
            return

        candidate = (token[1:] if has_slash else token).split("@", maxsplit=1)[0].lower()
        if not candidate:
            return

        is_admin_user = await users.is_admin(sender_id)
        commands = get_all_commands() if is_admin_user else get_user_commands()
        descriptions = dict(commands)
        visible_commands = [cmd for cmd, _ in commands]

        # If the user didn't use '/', only respond when it looks like a command attempt.
        if not has_slash and candidate not in visible_commands:
            rough = difflib.get_close_matches(candidate, visible_commands, n=1, cutoff=0.60)
            if not rough:
                return

        suggestions = difflib.get_close_matches(candidate, visible_commands, n=3, cutoff=0.45)

        if candidate in visible_commands and candidate not in suggestions:
            suggestions = [candidate] + suggestions

        if suggestions:
            suggestion_lines = "\n".join(
                f"• /{cmd} - {descriptions.get(cmd, '')}" for cmd in suggestions
            )
            prefix = "ℹ️ Commands start with '/'.\n\n" if not has_slash else ""
            text = (
                f"{prefix}❓ {token} is an unknown command.\n\n"
                "Did you mean:\n"
                f"{suggestion_lines}\n\n"
                "Use /help to see all commands."
            )
        else:
            text = (
                f"❓ {token} is an unknown command.\n\n"
                "Use /help to see all commands."
            )

        await self.api.message_manager.send_text(sender_id, text, True, True)

    @dep.verify_user
    async def handle_complete_session_command(self, event: events.NewMessage.Event) -> None:
        """Handle the /complete_session command to finalize a coffee session."""
        user_id = event.sender_id
        log_telegram_command(user_id, "/complete_session", getattr(event, "chat_id", None))

        try:
            session = await self.api.session_manager.get_active_session()

            if session is None:
                await self.api.message_manager.send_text(
                    user_id,
                    "❌ You don't have an active coffee session.",
                    True,
                    True,
                )
                return

            try:
                completed = await self.api.session_manager.complete_session(user_id)
            except Exception:
                return

            if not completed:
                await self.api.message_manager.send_text(
                    user_id,
                    "❌ No active session to complete.",
                    True,
                    True,
                )
                return

            for _, group_member in session.group_state.members.items():
                if group_member.coffee_count <= 0:
                    continue

                if group_member.user_id is None:
                    continue

                await session.fetch_link("initiator")
                initiator_display_name = session.initiator.display_name  # type: ignore

                await self.api.message_manager.send_text(
                    group_member.user_id,
                    f"{initiator_display_name} has ordered {group_member.coffee_count} coffees for you.\n",
                    True,
                    True,
                )

        except Exception as e:
            await self.api.message_manager.send_text(
                user_id,
                f"❌ Failed to complete session: {str(e)}",
                True,
                True,
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
            # Start the completion conversation (handles confirmation if needed)
            success = await self.api.conversation_manager.close_card_conversation(user_id)
            
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
        
        # Start the debt conversation
        await self.api.conversation_manager.debt_conversation(user_id)

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
            "• /start - Register with the bot\n\n"
            
            "**Coffee Orders:**\n"
            "• /order - Create or join a session to place an order\n"
            "• `send a number` - Quick order for yourself (e.g. send `2` → confirm)\n"

            "**Coffee Cards:**\n"
            "• /cards - Show status and manage all coffee cards\n"
            "• /new_card - Create a new coffee card that you paid for\n"
            "• /close_card - Close the last active coffee card\n\n"
            
            "**Finances:**\n"
            "• /debt - Show and manage your debts\n"
            "• /credit - Display and manage debts others owe to you\n"
            "• /paypal - Setup your paypal.me link\n\n"
            
            "**Settings:**\n"
            "• /settings - Adjust your personal preferences\n"
            "  📋 **Ordering:** Page size, group sorting\n"
            "  💬 **Vanishing Messages:** Auto-cleanup, threshold\n"
            "  🔧 **Administration (Admins Only):**\n"
            "     📊 Logging settings\n"
            "     🔔 Notification preferences\n"
            "• /profile - Manage your own Information\n\n"
            
            "**Managment (Admins Only):**\n"
            "• /sync - Export current state to Google Sheets\n"
            "• /snapshots - Create and restore snapshots\n"
            "• /users - Manage users\n\n"
            
            "**Other:**\n"
            "• /help - Show this help message\n\n"
        )
        
        await self.api.message_manager.send_text(
            user_id,
            help_text,
            vanish=True,
            conv=True,
            link_preview=False
        )

