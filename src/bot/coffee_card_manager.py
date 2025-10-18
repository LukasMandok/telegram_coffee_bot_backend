import asyncio

from typing import List, Dict, Any, TYPE_CHECKING, Optional
from datetime import datetime

from src.exceptions.coffee_exceptions import InsufficientCoffeeCardCapacityError, UserNotFoundError

from ..models.coffee_models import CoffeeCard, CoffeeOrder, CoffeeSession, ConsumerStats, UserDebt
from ..services.order import place_order
from ..models.beanie_models import TelegramUser, PassiveUser

from ..dependencies.dependencies import repo, get_repo
from ..utils.beanie_utils import requires_beanie

from src.common.log import log_coffee_card_created
from .keyboards import KeyboardManager

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
        self.available = 0

    @requires_beanie(CoffeeCard)
    async def load_from_db(self):
        """Load active coffee cards from database."""
        self.cards = await CoffeeCard.find(CoffeeCard.is_active == True).to_list()
        await self._update_available()
        print(f"✅ Loaded {len(self.cards)} active coffee cards from database")

    async def _update_available(self):
        self.available = sum(card.remaining_coffees for card in self.cards)



    async def _add_coffee_card(self, card: CoffeeCard):
        if card not in self.cards:
            self.cards.append(card)
            await self._update_available()
        else:
            raise ValueError("Card already exists in manager")
        
    async def _deactivate_coffee_card(self, card: CoffeeCard):    
        card.is_active = False
        await card.save()

        if card in self.cards:
            self.cards.remove(card)
            await self._update_available()
        else:
            raise ValueError("Card not found in manager")
        

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

        print(f"Coffee card created: {card}")

        await card.insert()
        log_coffee_card_created(card_name, total_coffees, purchaser_id, cost_per_coffee)

        await self._add_coffee_card(card)

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
        
        if available > quantity:
            return cards
        else:
            raise ValueError("Not enough available coffee")

    async def get_active_coffee_cards(self) -> List[CoffeeCard]:
        """Get all active coffee cards."""
        return self.cards
    
    # TODO: implement correctly 
    async def get_user_coffee_cards(self, user_id: int) -> List[CoffeeCard]:
        """Get all coffee cards purchased by a user."""
        user = await TelegramUser.find_one(TelegramUser.user_id == user_id)
        if not user:
            return []
        return await CoffeeCard.find(CoffeeCard.purchaser == user, 
                                    fetch_links=True).to_list()
    
    async def close_card(
        self, 
        card: CoffeeCard,  
        requesting_user_id: Optional[int] = None,
        require_confirmation: bool = False  # Deprecated - confirmation should be handled in conversation
    ) -> List[UserDebt]:
        """
        Mark a coffee card as completed and create debt records.
        
        Note: Confirmation logic has been moved to ConversationManager.close_card_conversation()
        
        Args:
            card: The coffee card to complete
            requesting_user_id: Optional user ID who is requesting completion (for notifications)
            require_confirmation: Deprecated - confirmation should be handled before calling this method
            
        Returns:
            List of created UserDebt documents
            
        Raises:
            ValueError: If card not found or already completed
        """        
        if not card:
            raise ValueError(f"Card not provided")
        
        
        await card.fetch_link("purchaser")
        purchaser: TelegramUser = card.purchaser  # type: ignore        
        
        # Check if card is already completed
        if not card.is_active:
            raise ValueError(f"Card '{card.name}' is already completed")
        
        # Create or update debts FIRST (before deactivating card)
        # because debt_manager checks if card is active
        debts = await self.api.debt_manager.create_or_update_debts_for_card(card)
        
        # NOW mark card as completed and deactivate
        await self._deactivate_coffee_card(card)
        
        print(f"✅ Card '{card.name}' marked as completed")
        
        # Build debt summary message for purchaser
        if debts:
            debt_summary = "\n".join([
                f"• {d.debtor.display_name}: €{d.total_amount:.2f} ({d.total_coffees} coffees)"  # type: ignore
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
            f"💰 Total to Collect: **€{total_debt:.2f}**\n\n"
        )
        
        if debts:
            purchaser_message += f"**Who Owes You:**\n{debt_summary}\n\n"
            
            # Add payment link if available
            if purchaser.paypal_link:
                purchaser_message += f"💳 Your Payment Link:\n{purchaser.paypal_link}\n\n"
                purchaser_message += "Share this link with people who owe you money!"
            else:
                purchaser_message += "💡 Set up your payment link with /paypal to easily collect money!"
        else:
            purchaser_message += "No one owes you money for this card. ☕"
        
        await self.api.message_manager.send_text(
            purchaser.user_id,
            purchaser_message,
            vanish=False,
            conv=False,
            silent=False
        )
        
        # Notify all consumers who have debts
        for debt in debts:
            # Debtor should already be loaded from create_or_update_debts_for_card
            debtor = debt.debtor  # type: ignore
            
            # Skip if debtor has no user_id (shouldn't happen for TelegramUsers)
            if not hasattr(debtor, 'user_id') or not debtor.user_id:
                continue
            
            debtor_message = (
                f"💳 **Coffee Card Completed!**\n\n"
                f"📋 Card: **{card.name}**\n"
                f"☕ You drank: **{debt.total_coffees} coffees**\n"
                f"💰 You owe: **€{debt.total_amount:.2f}** to: **{purchaser.display_name}**\n"
            )
            
            # Add payment link with amount if available
            if purchaser.paypal_link:
                # PayPal.me supports amount parameter: https://paypal.me/username/amount
                payment_link_with_amount = f"{purchaser.paypal_link}/{debt.total_amount:.2f}EUR"
                debtor_message += f"\n💳 **Payment Link:**\n{payment_link_with_amount}\n\n"
                debtor_message += "Click the link to pay directly!"
            else:
                debtor_message += f"\n💡 Contact {purchaser.display_name} for payment details."
            
            await self.api.message_manager.send_text(
                debtor.user_id,
                debtor_message,
                vanish=False,
                conv=False,
                silent=False
            )
        
        # If someone else completed it manually, notify them too
        if requesting_user_id and requesting_user_id != purchaser.user_id:
            await self.api.message_manager.send_text(
                requesting_user_id,
                f"✅ **Card Completed!**\n\n"
                f"📋 Card: **{card.name}**\n"
                f"👤 Purchaser: {purchaser.display_name}\n"
                f"💰 Total Debts: €{total_debt:.2f}\n\n"
                f"All debtors have been notified.",
                vanish=False,
                conv=False,
                silent=False
            )
        
        print(f"✅ Card '{card.name}' completed with {len(debts)} debts created, {len(debts)} notifications sent")
        
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
            raise InsufficientCoffeeCardCapacityError(
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
            raise InsufficientCoffeeCardCapacityError(
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
        order = await place_order(
            initiator=initiator,
            consumer=consumer,
            cards=[card],
            quantity=1,
            from_session=False,
            session=None,
            enforce_capacity=False
        )
        
        await self._update_available()
        return order
    
    def allocate_session_orders(self, session: CoffeeSession) -> Dict[str, List[CoffeeCard]]:
        """
        Allocate coffee cards to each member's order in a session.
        
        Returns a dict mapping member display names to the list of cards allocated for their orders.
        Raises InsufficientCoffeeCardCapacityError if not enough coffees available.
        """
        total_needed = sum(member.coffee_count for member in session.group_state.members.values())
        
        if self.available < total_needed:
            raise InsufficientCoffeeCardCapacityError(
                requested=total_needed,
                available=self.available
            )
        
        # Build allocation plan: {member_name: [cards]}
        allocations: Dict[str, List[CoffeeCard]] = {}
        
        # Use cards in natural order (creation date) - self.cards is already sorted by insertion
        card_idx = 0
        
        for member_name, member_data in session.group_state.members.items():
            if member_data.coffee_count == 0:
                continue
                
            needed = member_data.coffee_count
            member_cards = []
            
            while needed > 0 and card_idx < len(self.cards):
                card = self.cards[card_idx]
                
                if card.remaining_coffees > 0:
                    # This card can contribute
                    member_cards.append(card)
                    
                    if card.remaining_coffees >= needed:
                        # This card covers the rest
                        needed = 0
                    else:
                        # Take what we can and move to next card
                        needed -= card.remaining_coffees
                        card_idx += 1
                else:
                    # Card empty, skip it
                    card_idx += 1
            
            if needed > 0:
                # Shouldn't happen if we validated available capacity
                raise InsufficientCoffeeCardCapacityError(
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
                print(f"⚠️  Warning: Consumer '{member_name}' not found, skipping order")
                continue
            
            # Get the quantity this member ordered from the session
            member_data = session.group_state.members.get(member_name)
            if not member_data:
                print(f"⚠️  Warning: Member '{member_name}' not found in session data")
                continue
            
            quantity = member_data.coffee_count
            if quantity == 0:
                continue
            
            # Create order with integrated debt tracking
            order = await place_order(
                initiator=initiator,
                consumer=consumer,
                cards=cards,
                quantity=quantity,
                from_session=True,
                session=session,
                enforce_capacity=True
            )
            
            orders_created.append(order)
        
        # Update cached available count
        await self._update_available()
        
        return orders_created
