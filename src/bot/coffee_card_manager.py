import asyncio

from typing import List, Dict, Any, TYPE_CHECKING, Optional

from src.exceptions.coffee_exceptions import InsufficientCoffeeCardCapacityError, UserNotFoundError

from ..models.coffee_models import CoffeeCard, CoffeeOrder, CoffeeSession
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
        
        
    ### Orders
    
    async def create_coffee_order(
        self,
        initiator_id: int,
        consumer_name: str,
        quantity: int,
        session: Optional[CoffeeSession] = None
    ) -> CoffeeOrder:
        """Create a new coffee order."""
        repo = get_repo()
        
        available = await self.get_available()
        if available < quantity:
            raise InsufficientCoffeeCardCapacityError(
                requested=quantity,
                available=available
            )

        cards = await self.get_coffee_cards_for_order(quantity)
        
    # TODO: use a function to find a TelegramUser
        # Look up by Telegram user_id field (Document.get expects an ObjectId)
        initiator: TelegramUser = await repo.find_user_by_id(initiator_id)

        consumer: PassiveUser = await repo.find_user_by_display_name(consumer_name)

        # Create the order
        order = CoffeeOrder(
            coffee_cards=cards,
            initiator=initiator,
            consumer=consumer,
            quantity=quantity,
            session=None  # Explicitly set as None for individual orders
        )

        await order.insert()
        await self._update_available()

        return order