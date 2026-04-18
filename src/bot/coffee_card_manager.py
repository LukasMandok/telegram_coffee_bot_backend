import asyncio

from typing import List, Dict, Any, TYPE_CHECKING, Optional
from datetime import datetime

from beanie.odm.fields import Link as BeanieLink

from src.exceptions.coffee_exceptions import InsufficientCoffeeError, UserNotFoundError

from ..models.coffee_models import CoffeeCard, CoffeeOrder, CoffeeSession, ConsumerStats, UserDebt
from ..services.order import place_order
from ..models.beanie_models import TelegramUser, PassiveUser

from ..dependencies.dependencies import repo, get_repo
from ..utils.beanie_utils import requires_beanie

from src.common.log import log_coffee_card_created, Logger
from .keyboards import KeyboardManager
from .message_flow import ButtonCallback
from .message_flow_ids import DebtQuickConfirmCallbacks
from .message_flow_helpers import format_money
from ..services.gsheet_sync import request_gsheet_sync_after_action
from ..database.snapshot_manager import get_current_pending_snapshot, pending_snapshot

# Type-only imports - only needed for type annotations
if TYPE_CHECKING:
    from ..database.base_repo import BaseRepository

class CoffeeCardManager:
    """Instance manager for a set of coffee cards.

    Use like: manager = CoffeeCardManager(cards)
    """
    def __init__(self, api):
        self.api = api
        
        self.cards = []
        self.logger = Logger("CoffeeCardManager")
        self.available = 0

    @requires_beanie(CoffeeCard)
    async def load_from_db(self):
        """Load active coffee cards from database."""
        self.cards = await CoffeeCard.find(CoffeeCard.is_active == True).sort("created_at").to_list()  # type: ignore[comparison-overlap]
        await self._update_available()
        self.logger.info(f"Loaded {len(self.cards)} active coffee cards from database")

    async def _update_available(self):
        self.available = sum(card.remaining_coffees for card in self.cards)


    async def find_orders_for_card(self, card: CoffeeCard) -> List[CoffeeOrder]:
        """Fetch all orders that used the given card (newest first).

        Kept on the manager because it is DB access and used by multiple UIs.
        """
        if card.id is None:
            return []

        orders_by_id: Dict[str, CoffeeOrder] = {}

        query_variants: List[Dict[str, Any]] = [
            {"coffee_cards.$id": card.id},
            {"coffee_cards": card.id},
            {"coffee_cards._id": card.id},
        ]

        for query in query_variants:
            matched: List[CoffeeOrder] = (
                await CoffeeOrder.find(query, fetch_links=True).sort("-order_date").to_list()
            )  # type: ignore[arg-type]
            for order in matched:
                if order.id is None:
                    continue
                orders_by_id[str(order.id)] = order

        # Fallback: if card keeps an explicit order list, merge it too.
        try:
            await card.fetch_link("orders")
        except Exception:
            pass

        for linked in card.orders:
            if isinstance(linked, CoffeeOrder) and linked.id is not None:
                orders_by_id.setdefault(str(linked.id), linked)

        return sorted(orders_by_id.values(), key=lambda o: o.order_date, reverse=True)


    async def find_orders_for_session(self, session: CoffeeSession) -> List[CoffeeOrder]:
        """Fetch all orders for a session (newest first)."""
        if session.id is None:
            return []

        orders_by_id: Dict[str, CoffeeOrder] = {}

        query_variants: List[Dict[str, Any]] = [
            {"session.$id": session.id},
            {"session": session.id},
            {"session._id": session.id},
        ]

        for query in query_variants:
            matched: List[CoffeeOrder] = (
                await CoffeeOrder.find(query, fetch_links=False).sort("-order_date").to_list()
            )  # type: ignore[arg-type]
            for order in matched:
                if order.id is None:
                    continue
                orders_by_id[str(order.id)] = order

        # Fallback: merge explicit session order list.
        try:
            await session.fetch_link("orders")
        except Exception:
            pass

        for linked in session.orders:
            if isinstance(linked, CoffeeOrder) and linked.id is not None:
                orders_by_id.setdefault(str(linked.id), linked)

        return sorted(orders_by_id.values(), key=lambda o: o.order_date, reverse=True)


    async def find_debts_for_card(self, card: CoffeeCard) -> List[UserDebt]:
        """Fetch all debts belonging to a card (latest updated first)."""
        if card.id is None:
            return []

        debts_by_id: Dict[str, UserDebt] = {}

        query_variants: List[Dict[str, Any]] = [
            {"coffee_card.$id": card.id},
            {"coffee_card": card.id},
            {"coffee_card._id": card.id},
        ]

        for query in query_variants:
            matched: List[UserDebt] = (
                await UserDebt.find(query, fetch_links=True).sort("-updated_at").to_list()
            )  # type: ignore[arg-type]
            for debt in matched:
                if debt.id is None:
                    continue
                debts_by_id[str(debt.id)] = debt

        debts = sorted(debts_by_id.values(), key=lambda d: d.updated_at, reverse=True)

        # Ensure debtor links are resolved even for historical DBRef variants.
        for debt in debts:
            try:
                await debt.fetch_link("debtor")
            except Exception:
                pass

            if isinstance(debt.debtor, BeanieLink) and debt.debtor.ref is not None:
                ref = debt.debtor.ref
                if ref.collection == TelegramUser.Settings.name:
                    resolved = await TelegramUser.get(ref.id)
                    if resolved is not None:
                        debt.debtor = resolved  # type: ignore[assignment]
                elif ref.collection == PassiveUser.Settings.name:
                    resolved = await PassiveUser.get(ref.id)
                    if resolved is not None:
                        debt.debtor = resolved  # type: ignore[assignment]

        return debts



    async def _add_coffee_card(self, card: CoffeeCard):
        if card not in self.cards:
            self.cards.append(card)
            await self._update_available()
        else:
            raise ValueError("Card already exists in manager")
        
    async def _deactivate_coffee_card(self, card: CoffeeCard):    
        card.is_active = False
        card.completed_at = datetime.now()
        await card.save()

        removed_from_cache = False
        if card.id is not None:
            for existing in list(self.cards):
                if existing.id == card.id:
                    self.cards.remove(existing)
                    removed_from_cache = True
                    break
        else:
            # Shouldn't happen in practice, but keep a safe fallback.
            if card in self.cards:
                self.cards.remove(card)
                removed_from_cache = True

        if not removed_from_cache:
            # IMPORTANT: do not raise here. The DB update already happened; raising would
            # incorrectly report a failure and abort snapshot creation.
            self.logger.debug(
                f"Card deactivated but not found in manager cache (likely different document instance). name='{card.name}'"
            )

        await self._update_available()
    
    async def _update_inactive_counters(self, card: CoffeeCard) -> None:
        """
        Update inactive_card_count for all users when a card is closed.
        
        Users who ordered on this card get their counter reset to 0.
        Users who didn't order get their counter incremented by 1.
        Users with inactive_card_count >= 2 get archived.
        Users with inactive_card_count >= 10 get disabled.
        
        Args:
            card: The coffee card that is being closed
        """
        repo = get_repo()
        
        # Get all users (both TelegramUser and PassiveUser)
        all_users = await repo.find_all_users() or []
        
        # Get set of user IDs who ordered on this card (from consumer_stats)
        users_who_ordered = set(card.consumer_stats.keys())
        
        # Update each user's inactive counter
        for user in all_users:
            user_id = user.stable_id
            
            if user_id in users_who_ordered:
                # User ordered on this card - reset counter to 0
                if user.inactive_card_count != 0:
                    user.inactive_card_count = 0
                    # Unarchive and re-enable if they were archived/disabled
                    if user.is_archived:
                        user.is_archived = False
                    if user.is_disabled:
                        user.is_disabled = False
                    await user.save()
                    self.logger.info(f"Reset inactive counter for {user.display_name} (ordered on card)")
            else:
                # User didn't order - increment counter
                user.inactive_card_count += 1
                self.logger.info(f"Incremented inactive counter for {user.display_name} to {user.inactive_card_count}")
                
                # TODO: move this to the settigs
                # Disable if counter reaches 10
                if user.inactive_card_count >= 10 and not user.is_disabled:
                    user.is_disabled = True
                    user.is_archived = True  # Also mark as archived
                    self.logger.info(f"Disabled {user.display_name} (inactive for {user.inactive_card_count} cards)")
                # Archive if counter reaches 2 (but not disabled)
                elif user.inactive_card_count >= 2 and not user.is_archived and not user.is_disabled:
                    user.is_archived = True
                    self.logger.info(f"Archived {user.display_name} (inactive for {user.inactive_card_count} cards)")
                
                await user.save()
        

    @pending_snapshot(
        "card_created",
        reason="Coffee Card Created",
        collections=("coffee_cards",),
    )
    async def create_coffee_card(
        self,
        total_coffees: int,
        cost_per_coffee: float,
        purchaser_id: int
    ) -> CoffeeCard:
        """Create a new coffee card."""
        repo = get_repo()
        purchaser = await repo.find_user_by_id(purchaser_id)
        if not purchaser:
            raise ValueError("Purchaser not found")

        # Count existing cards for naming
        existing_cards = await CoffeeCard.find().to_list()
        card_number = len(existing_cards) + 1
        card_name = f"Card {card_number}"

        total_cost = float(total_coffees) * cost_per_coffee

        card = CoffeeCard(
            name=card_name,
            total_coffees=total_coffees,
            remaining_coffees=total_coffees,
            cost_per_coffee=cost_per_coffee,
            total_cost=total_cost,
            purchaser=purchaser
        )

        await card.insert()
        log_coffee_card_created(card_name, total_coffees, purchaser_id, cost_per_coffee)
        self.logger.info(
            f"Coffee card created: name='{card_name}', total_coffees={total_coffees}, "
            f"cost_per_coffee=€{cost_per_coffee:.2f}, purchaser={purchaser.display_name} (id={purchaser_id})"
        )

        await self._add_coffee_card(card)

        request_gsheet_sync_after_action(reason="card_created")

        # Notify admins about new card creation (background notification, respects notification settings).
        try:
            admin_ids = await repo.get_registered_admins()
            if admin_ids and getattr(self.api, "message_manager", None) is not None:
                purchaser_name = purchaser.display_name or str(purchaser_id)
                message = (
                    "🆕 **New coffee card created**\n\n"
                    f"Card: **{card.name}**\n"
                    f"Total coffees: **{card.total_coffees}**\n"
                    f"Cost per coffee: **{format_money(card.cost_per_coffee)}**\n"
                    f"Total cost: **{format_money(card.total_cost)}**\n"
                    f"Purchaser: **{purchaser_name}**"
                )
                for admin_id in admin_ids:
                    if int(admin_id) == int(purchaser_id):
                        continue
                    await self.api.message_manager.send_user_notification(int(admin_id), message)
        except Exception as exc:
            self.logger.warning(
                "Failed to notify admins about new card creation",
                extra_tag="CARD",
                exc=exc,
            )

        return card
    
    async def get_available(self) -> int:
        """Get the total number of available coffees across all cards."""
        return self.available

    async def get_coffee_cards_for_order(self, quantity: int) -> List[CoffeeCard]:
        """Get all active coffee cards that can cover the given quantity."""
        available = 0
        cards = []
        for card in self.cards:
            if quantity > available:
                available += card.remaining_coffees
                cards.append(card)
            else:
                break
        
        if available >= quantity:
            return cards
        else:
            raise ValueError("Not enough available coffee")

    async def get_active_coffee_cards(self) -> List[CoffeeCard]:
        """Get all active coffee cards."""
        return self.cards

    async def get_oldest_active_coffee_card(self) -> Optional[CoffeeCard]:
        """Get the oldest active coffee card from the manager cache."""
        if not self.cards:
            return None
        return min(self.cards, key=lambda card: card.created_at)
    
    # TODO: implement correctly 
    async def get_user_coffee_cards(self, user_id: int) -> List[CoffeeCard]:
        """Get all coffee cards purchased by a user."""
        user = await TelegramUser.find_one(TelegramUser.user_id == user_id)
        if not user:
            return []
        return await CoffeeCard.find(CoffeeCard.purchaser == user, 
                                    fetch_links=True).to_list()
    
    @pending_snapshot(
        lambda self, card, **_: f"card_closed:{str(card.id or 'unknown')}",
        reason=lambda self, card, **_: f"Close Coffee Card ({card.name})",
        collections=("coffee_cards", "user_debts", "payments"),
    )
    async def close_card(
        self, 
        card: CoffeeCard,  
        requesting_user_id: Optional[int] = None,
        closed_by_session: bool = False,
    ) -> List[UserDebt]:
        """
        Mark a coffee card as completed and create debt records.
        
        Note: Confirmation logic has been moved to ConversationManager.close_card_conversation()
        
        Args:
            card: The coffee card to complete
            requesting_user_id: Optional user ID who is requesting completion (for notifications)            
        Returns:
            List of created UserDebt documents
            
        Raises:
            ValueError: If card not found or already completed
        """        
        if not card:
            raise ValueError(f"Card not provided")

        if card.id is not None:
            for existing in self.cards:
                if existing.id == card.id:
                    card = existing
                    break

        pending = get_current_pending_snapshot()
        if pending is not None:
            context_key = pending.context.split(":", 1)[0] if pending.context else "(none)"
            self.logger.debug(
                f"Pending snapshot active for close_card: context_key={context_key}, collections={list(pending.collections)}"
            )

        self.logger.debug(
            f"Closing coffee card requested: name='{card.name}', requesting_user_id={requesting_user_id}, closed_by_session={closed_by_session}"
        )

        await card.fetch_link("purchaser")
        purchaser: TelegramUser = card.purchaser  # type: ignore        
        
        # Check if card is already completed
        if not card.is_active:
            raise ValueError(f"Card '{card.name}' is already completed")
        
        try:
            debts = await self.api.debt_manager.create_or_update_debts_for_card(card)
            self.logger.info(
                f"Debts created/updated for card close. name='{card.name}', debts={len(debts)}"
            )

            await self._update_inactive_counters(card)
            await self._deactivate_coffee_card(card)

            request_gsheet_sync_after_action(reason="card_closed")
            self.logger.info(f"Card '{card.name}' marked as completed")
        except Exception as exc:
            self.logger.error(
                f"Failed while closing coffee card. name='{card.name}'",
                exc=exc,
            )
            raise
        
        # Build debt summary message for purchaser
        if debts:
            debt_summary = "\n".join([
                f"• {d.debtor.display_name}: {format_money(d.total_amount)} ({d.total_coffees} coffees)"  # type: ignore
                for d in debts
            ])
            total_debt = sum(d.total_amount for d in debts)
        else:
            debt_summary = "No debts (only you consumed coffees)"
            total_debt = 0.0
        
        # Notify the purchaser
        notification_prefix = "🎉 **Card Auto-Completed!**" if not requesting_user_id else "🎉 **Card Completed!**"
        
        purchaser_message = (
            f"{notification_prefix}\n\n"
            f"📋 Card: **{card.name}**\n"
            f"💰 Total to Collect: **{format_money(total_debt)}**\n\n"
        )
        
        if debts:
            purchaser_message += f"**Who Owes You:**\n{debt_summary}\n\n"
            
            # Add payment link if available
            # TODO: not sure if this paypal_link makes sense here
            if purchaser.paypal_link:
                purchaser_message += f"💳 Your Payment Link:\n{purchaser.paypal_link}\n\n"
                purchaser_message += "Share this link with people who owe you money!"
            else:
                purchaser_message += "💡 Set up your payment link with /paypal to easily collect money!"
        else:
            purchaser_message += "No one owes you money for this card. ☕"
        
        # Purchaser message is a direct reply only when the purchaser initiated the completion.
        if requesting_user_id == purchaser.user_id and not closed_by_session:
            await self.api.message_manager.send_text(
                purchaser.user_id,
                purchaser_message,
                vanish=False,
                conv=False,
                silent=False,
            )
        else:
            await self.api.message_manager.send_user_notification(
                purchaser.user_id,
                purchaser_message,
            )
        
        # Notify all consumers who have debts
        for debt in debts:
            # Debtor should already be loaded from create_or_update_debts_for_card
            debtor = debt.debtor  # type: ignore
            
            # Skip if debtor has no user_id (shouldn't happen for TelegramUsers)
            if not isinstance(debtor, TelegramUser):
                continue
            
            debtor_message = (
                f"💳 **Coffee Card Completed!**\n\n"
                f"📋 Card: **{card.name}**\n"
                f"☕ You drank: **{debt.total_coffees} coffees**\n"
                f"💰 You owe: **{format_money(debt.total_amount)}** to: **{purchaser.display_name}**\n"
            )
            
            # Add payment link with amount if available
            if purchaser.paypal_link:
                # PayPal.me supports amount parameter: https://paypal.me/username/amount
                payment_link_with_amount = f"{purchaser.paypal_link}/{debt.total_amount:.2f}EUR"
                debtor_message += f"\n💳 **Payment Link:**\n{payment_link_with_amount}\n\n"
                debtor_message += "Click the link to pay directly!"
            else:
                debtor_message += f"\n💡 Contact {purchaser.display_name} for payment details."
            
            await self.api.message_manager.send_user_notification(
                debtor.user_id,
                debtor_message,
            )

            await self.api.message_manager.send_user_notification_keyboard(
                debtor.user_id,
                DebtQuickConfirmCallbacks.QUESTION_TEXT,
                buttons=[
                    [
                        ButtonCallback(
                            DebtQuickConfirmCallbacks.YES_TEXT,
                            f"{DebtQuickConfirmCallbacks.YES_PREFIX}{debtor.user_id}:{debt.id}",
                        ),
                        ButtonCallback(
                            DebtQuickConfirmCallbacks.NO_TEXT,
                            f"{DebtQuickConfirmCallbacks.NO_PREFIX}{debtor.user_id}:{debt.id}",
                        ),
                    ]
                ],
            )
        
        # # If someone else completed it manually, notify them too
        # if requesting_user_id and requesting_user_id != purchaser.user_id:
        #     await self.api.message_manager.send_text(
        #         requesting_user_id,
        #         f"✅ **Card Completed!**\n\n"
        #         f"📋 Card: **{card.name}**\n"
        #         f"👤 Purchaser: {purchaser.display_name}\n"
        #         f"💰 Total Debts: €{total_debt:.2f}",
        #         vanish=False,
        #         conv=False,
        #         silent=False
        #     )
        
        self.logger.info(f"Card '{card.name}' completed with {len(debts)} debts created, {len(debts)} notifications sent")
        
        return debts
        
        
    ### Orders
    
    # Order creation logic has been moved to services.order.place_order

    
    async def create_coffee_order(
        self,
        initiator_id: int,
        consumer_name: str,
        quantity: int = 1
    ) -> CoffeeOrder:
        """
        Create a new individual coffee order (not part of a session).
        Individual orders are always 1 coffee from 1 card.
        
        Args:
            initiator_id: User ID who initiated the order
            consumer_name: Display name of the consumer
            quantity: Always 1 for individual orders (kept for compatibility)
        
        Returns:
            Created CoffeeOrder
        """
        repo = get_repo()
        
        # For individual orders, we only support 1 coffee at a time
        if quantity != 1:
            raise ValueError("Individual orders must be for exactly 1 coffee. Use sessions for multiple coffees.")
        
        # Check availability
        if self.available < 1:
            raise InsufficientCoffeeError(
                requested=1,
                available=self.available
            )

        # Get the first available card
        card = None
        for c in self.cards:
            if c.remaining_coffees > 0:
                card = c
                break
        
        if not card:
            raise InsufficientCoffeeError(
                requested=1,
                available=0
            )
        
        # Look up users
        initiator = await repo.find_user_by_id(initiator_id)
        if not initiator:
            raise ValueError(f"Initiator {initiator_id} not found")

        consumer = await repo.find_user_by_display_name(consumer_name)
        if not consumer:
            raise ValueError(f"Consumer {consumer_name} not found")

        # Create order with integrated debt tracking
        orders = await place_order(
            initiator=initiator,
            consumer=consumer,
            cards=[card],
            quantity=1,
            session=None,
            enforce_capacity=False
        )

        order = orders[0]

        if bool(card.is_active) and int(card.remaining_coffees) == 0:
            self.logger.debug(
                f"Auto-completing card after individual order: {card.name}",
                extra_tag="QuickOrder",
            )
            await self.close_card(
                card,
                requesting_user_id=initiator_id,
                closed_by_session=False,
            )
        else:
            await self._update_available()

        request_gsheet_sync_after_action(reason="quick_order_completed")
        return order
    
    def allocate_session_orders(self, session: CoffeeSession) -> Dict[str, List[CoffeeCard]]:
        """
        Allocate coffee cards to each member's order in a session.
        
        Returns a dict mapping member display names to the list of cards allocated for their orders.
        Raises InsufficientCoffeeError if not enough coffees available.
        """
        total_needed = sum(member.coffee_count for member in session.group_state.members.values())
        
        if self.available < total_needed:
            raise InsufficientCoffeeError(
                requested=total_needed,
                available=self.available
            )
        
        # Build allocation plan: {member_name: [cards]}
        allocations: Dict[str, List[CoffeeCard]] = {}
        
        # Use cards in natural order (creation date) - self.cards is already sorted by insertion.
        # IMPORTANT: We must "reserve" capacity while allocating; otherwise later members may be
        # assigned to a card that earlier members already exhausted.
        card_idx = 0
        remaining_by_card = [max(0, int(c.remaining_coffees)) for c in self.cards]
        
        for member_name, member_data in session.group_state.members.items():
            if member_data.coffee_count == 0:
                continue
                
            needed = member_data.coffee_count
            member_cards = []
            
            while needed > 0 and card_idx < len(self.cards):
                card = self.cards[card_idx]
                remaining_here = remaining_by_card[card_idx]

                if remaining_here <= 0:
                    card_idx += 1
                    continue

                member_cards.append(card)

                if remaining_here >= needed:
                    # This card covers the rest
                    remaining_by_card[card_idx] = remaining_here - needed
                    needed = 0
                else:
                    # Take what we can and move to next card
                    remaining_by_card[card_idx] = 0
                    needed -= remaining_here
                    card_idx += 1
            
            if needed > 0:
                # Shouldn't happen if we validated available capacity
                raise InsufficientCoffeeError(
                    requested=member_data.coffee_count,
                    available=member_data.coffee_count - needed
                )
            
            allocations[member_name] = member_cards
        
        return allocations
    
    async def create_orders_from_allocations(
        self, 
        allocations: Dict[str, List[CoffeeCard]], 
        initiator_id: int,
        session: CoffeeSession
    ) -> List[CoffeeOrder]:
        """
        Create CoffeeOrder documents from session allocation plan.
        This method is used exclusively for session-based orders.
        
        Args:
            allocations: Dict mapping member display names to their allocated cards
            initiator_id: User ID who initiated/submitted the session
            session: CoffeeSession to link orders to
            
        Returns:
            List of created CoffeeOrder documents
        """
        repo = get_repo()
        initiator = await repo.find_user_by_id(initiator_id)
        if not initiator:
            raise ValueError(f"Initiator {initiator_id} not found")
        
        orders_created = []
        
        for member_name, cards in allocations.items():
            if not cards:
                continue
            
            # Find the consumer by display name
            consumer = await repo.find_user_by_display_name(member_name)
            if not consumer:
                self.logger.warning(f"Consumer '{member_name}' not found, skipping order")
                continue
            
            # Get the quantity this member ordered from the session
            member_data = session.group_state.members.get(member_name)
            if not member_data:
                self.logger.warning(f"Member '{member_name}' not found in session data")
                continue
            
            quantity = member_data.coffee_count
            if quantity == 0:
                continue
            
            # Create order with integrated debt tracking
            orders = await place_order(
                initiator=initiator,
                consumer=consumer,
                cards=cards,
                quantity=quantity,
                session=session,
                enforce_capacity=True
            )

            orders_created.extend(orders)
        
        # Update cached available count
        await self._update_available()

        request_gsheet_sync_after_action(reason="session_completed")
        
        return orders_created
