import asyncio

from typing import List, Dict, Any, TYPE_CHECKING, Optional

from src.exceptions.coffee_exceptions import InsufficientCoffeeCardCapacityError, UserNotFoundError

from ..models.coffee_models import CoffeeCard, CoffeeOrder, CoffeeSession, ConsumerStats, UserDebt
from ..models.beanie_models import TelegramUser, PassiveUser

from ..dependencies.dependencies import repo, get_repo
from ..utils.beanie_utils import requires_beanie

from src.common.log import log_coffee_card_created

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
        name: str,
        total_coffees: int,
        cost_per_coffee: float,
        purchaser_id: int
    ) -> CoffeeCard:
        """Create a new coffee card."""
        repo = get_repo()
        print(f"Creating coffee card: {name}, {total_coffees} coffees at {cost_per_coffee} each, purchaser ID {purchaser_id}")
        purchaser = await repo.find_user_by_id(purchaser_id)
        if not purchaser:
            raise ValueError("Purchaser not found")

        total_cost = float(total_coffees) * cost_per_coffee

        card = CoffeeCard(
            name=name,
            total_coffees=total_coffees,
            remaining_coffees=total_coffees,
            cost_per_coffee=cost_per_coffee,
            total_cost=total_cost,
            purchaser=purchaser
        )
        
        print(f"Coffee card created: {card}")

        await card.insert()
        log_coffee_card_created(name, total_coffees, purchaser_id, cost_per_coffee)
        
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
    
    async def complete_coffee_card(self, card_id: str) -> List[UserDebt]:
        """
        Mark a coffee card as completed and create debt records.
        
        Args:
            card_id: ID of the card to complete
            
        Returns:
            List of created UserDebt documents
            
        Raises:
            ValueError: If card not found or already completed
        """
        from bson import ObjectId
        
        card = await CoffeeCard.get(ObjectId(card_id))
        if not card:
            raise ValueError(f"Card with ID {card_id} not found")
        
        # Use DebtManager to complete card and create debts
        debts = await self.api.debt_manager.complete_card_and_create_debts(card)
        
        return debts
        
        
    ### Orders
    
    async def _create_order_and_update_cards(
        self,
        initiator: TelegramUser,
        consumer: PassiveUser,
        cards: List[CoffeeCard],
        quantity: int,
        from_session: bool,
        session: Optional[CoffeeSession] = None
    ) -> CoffeeOrder:
        """
        Core method to create an order and update all related data.
        Handles: coffee deduction, order creation, card relationships, consumer stats, and debts.
        
        Args:
            initiator: User who initiated the order
            consumer: User who consumed the coffees
            cards: List of cards to use (in order of preference)
            quantity: Total number of coffees in this order
            from_session: Whether this is part of a session
            session: Optional session to link to
            
        Returns:
            Created CoffeeOrder
        """
        # Deduct coffees from cards and track per-card amounts
        remaining_to_deduct = quantity
        card_deductions: Dict[str, int] = {}
        
        for card in cards:
            if remaining_to_deduct == 0:
                break
                
            deduct_from_this_card = min(remaining_to_deduct, card.remaining_coffees)
            card.remaining_coffees -= deduct_from_this_card
            card_deductions[str(card.id)] = deduct_from_this_card
            remaining_to_deduct -= deduct_from_this_card
        
        # Create the order
        order = CoffeeOrder(
            coffee_cards=cards,  # type: ignore
            initiator=initiator,
            consumer=consumer,
            quantity=quantity,
            from_session=from_session
        )
        await order.insert()
        
        # Update each card's relationships and consumer stats
        consumer_key = consumer.stable_id  # Use stable_id as the key
        
        for card in cards:
            # Add order to card
            if order not in card.orders:
                card.orders.append(order)  # type: ignore
            
            # Update consumer stats (only for cards that actually contributed)
            coffees_from_this_card = card_deductions.get(str(card.id), 0)
            if coffees_from_this_card > 0:
                if consumer_key not in card.consumer_stats:
                    card.consumer_stats[consumer_key] = ConsumerStats(
                        user_id=consumer.stable_id,
                        display_name=consumer.display_name,
                        total_coffees=coffees_from_this_card,
                        last_order_date=order.order_date
                    )
                else:
                    stats = card.consumer_stats[consumer_key]
                    stats.total_coffees += coffees_from_this_card
                    stats.display_name = consumer.display_name  # Update display name in case it changed
                    stats.last_order_date = order.order_date
                
                # Note: Debts are created only when card is completed, not on each order
            
            await card.save()
        
        # Link to session if applicable
        if session:
            if order not in session.orders:
                session.orders.append(order)  # type: ignore
            
            # Add session to cards (only once per card per session)
            for card in cards:
                if session not in card.sessions:
                    card.sessions.append(session)  # type: ignore
                    await card.save()
        
        return order
    
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
        order = await self._create_order_and_update_cards(
            initiator=initiator,
            consumer=consumer,
            cards=[card],
            quantity=1,
            from_session=False,
            session=None
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
            order = await self._create_order_and_update_cards(
                initiator=initiator,
                consumer=consumer,
                cards=cards,
                quantity=quantity,
                from_session=True,
                session=session
            )
            
            orders_created.append(order)
        
        # Update cached available count
        await self._update_available()
        
        return orders_created
