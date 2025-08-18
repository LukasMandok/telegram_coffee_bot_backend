from typing import Optional, List, Dict, TYPE_CHECKING, Sequence, Self, Any
from datetime import datetime
from enum import Enum

from beanie import Document, BackLink, after_event, Update, before_event
from pydantic import Field, model_validator, field_validator
# from bson import Decimal128

from . import base_models as base
from .beanie_models import TelegramUser, FullUser
from ..bot.telethon_models import GroupState
from ..exceptions.coffee_exceptions import InvalidCoffeeCountError, InsufficientCoffeeError
from ..utils.typing_utils import Link


class PaymentMethod(str, Enum):
    CASH = "cash"
    BANK_TRANSFER = "bank_transfer"
    MANUAL = "manual"
    PAYPAL = "paypal"


class CoffeeCard(Document):
    """Represents a physical coffee card/cardboard."""
    
    name: str = Field(..., description="Name/identifier of the coffee card")
    total_coffees: int = Field(..., ge=0, description="Total number of coffees on the card")
    remaining_coffees: int = Field(..., ge=0, description="Remaining coffees on the card")
    cost_per_coffee: float = Field(..., gt=0, description="Cost per coffee in EUR")
    total_cost: float = Field(..., gt=0, description="Total cost of the card")
    purchaser: Link[FullUser] = Field(..., description="User who bought the card")
    created_at: datetime = Field(default_factory=datetime.now)
    is_active: bool = Field(default=True, description="Whether the card is still active")

    # Relationships - track both individual orders and group sessions  
    orders: List[Link["CoffeeOrder"]] = Field(default_factory=list, description="Orders made with this card")
    sessions: List[Link["CoffeeSession"]] = Field(default_factory=list, description="Sessions using this card")

    @field_validator('remaining_coffees')
    @classmethod  # TODO: check if this info.data actually works...
    def validate_remaining_coffees(cls, v, info):
        if 'total_coffees' in info.data and v > info.data['total_coffees']:
            raise ValueError('Remaining coffees cannot exceed total coffees')
        return v
    
    # @field_validator('cost_per_coffee', 'total_cost', mode='before')
    # @classmethod
    # def convert_decimal128_to_float(cls, v: Any) -> float:
    #     """Convert MongoDB Decimal128 to Python float for backward compatibility."""
    #     if isinstance(v, Decimal128):
    #         return float(str(v))
    #     elif isinstance(v, (int, float, str)):
    #         return float(v)
    #     else:
    #         raise ValueError(f"Cannot convert {type(v)} to float")
    

    
    def remove_coffee(self) -> bool:
        """Use one coffee from the card. Returns True if successful."""
        if self.remaining_coffees > 0:
            self.remaining_coffees -= 1
            return True
        return False
    
    def add_coffee(self) -> None:
        """Add one coffee back to the card (e.g., in case of cancellation)."""
        if self.remaining_coffees < self.total_coffees:
            self.remaining_coffees += 1
    
    class Settings:
        name = "coffee_cards"


class CoffeeOrder(Document):
    """Represents an individual coffee order."""
    consumer: Link[TelegramUser] = Field(..., description="User who consumed the coffee")
    initiator: Link[TelegramUser] = Field(..., description="User who executed/placed the order")
    coffee_card: Link[CoffeeCard] = Field(..., description="The card used for this order")

    quantity: int = Field(..., ge=1, description="Number of coffees ordered")
    order_date: datetime = Field(default_factory=datetime.now)
    
    # Link to session if this order was part of a group session
    from_session: bool = Field(default=False, description="Whether this order was part of a group session")
    session: Optional[Link["CoffeeSession"]] = Field(None, description="Session this order belongs to")
    
    class Settings:
        name = "coffee_orders"


class CoffeeSession(Document):
    """Represents a group coffee ordering session."""
    initiator: Link[TelegramUser] = Field(..., description="User who started the session")
    submitted_by: Optional[Link[TelegramUser]] = Field(None, description="User who submitted the session")
    
    # Support multiple coffee cards for large sessions
    coffee_cards: List[Link[CoffeeCard]] = Field(default_factory=list, description="Cards used for this session")
    
    # Session participants and their orders
    participants: List[Link[TelegramUser]] = Field(default_factory=lambda data: [data['initiator']],
                                                   description="Users participating in this session")

    session_date: datetime = Field(default_factory=datetime.now)
    completed_date: Optional[datetime] = Field(None, description="When the session was completed")
    is_active: bool = Field(default=True)
    
    # Integrated group state with coffee counts
    group_state: GroupState = Field(default_factory=lambda: GroupState(members={}), description="Group state with member coffee counts")
    
    # Generated orders from this session
    orders: List[Link[CoffeeOrder]] = Field(default_factory=list)
        
    class Settings:
        name = "coffee_sessions"
        
    
    async def get_available_coffees(self) -> int:
        """Get total available coffees across all cards in this session."""
        await self.fetch_link(self.coffee_cards)
        
        if not self.coffee_cards:
            return 0
            
        total_available = sum(card.remaining_coffees for card in self.coffee_cards) # type: ignore
        return total_available

    async def get_total_coffees(self) -> int:
        """Get total coffees ordered in this session."""
        return self.group_state.get_total_coffees()
    
    async def get_total_cost(self) -> float:
        """Calculate total cost for this session."""
        await self.fetch_link(self.coffee_cards)
        
        if not self.coffee_cards:
            return 0.0
            
        # For multiple cards, use the cost from the first card (or implement more complex logic)
        cost_per_coffee = self.coffee_cards[0].cost_per_coffee # type: ignore
        return float(await self.get_total_coffees()) * cost_per_coffee

    async def validate_coffee_counts(self, counts: Dict[str, int]) -> bool:
        """Validate coffee counts without persisting."""
        if any(count < 0 for count in counts.values()):
            raise InvalidCoffeeCountError("Coffee counts cannot be negative")
        
        total_requested = sum(counts.values())
        available = await self.get_available_coffees()
        
        if total_requested > available:
            raise InsufficientCoffeeError(total_requested, available)
        
        return True

    async def add_coffee_card(self, coffee_card: "CoffeeCard") -> None:
        """Add a coffee card to this session."""
        if coffee_card not in self.coffee_cards:
            self.coffee_cards.append(coffee_card)
    
    def add_coffee_for_member(self, member_name: str) -> None:
        """Add one coffee for a group member."""
        self.group_state.add_coffee(member_name)
    
    def remove_coffee_for_member(self, member_name: str) -> None:
        """Remove one coffee for a group member."""
        self.group_state.remove_coffee(member_name)
    

# TODO: check
class Payment(Document):
    """Represents a completed payment between users."""
    
    payer: Link[TelegramUser] = Field(..., description="User making the payment")
    recipient: Link[TelegramUser] = Field(..., description="User receiving the payment")
    amount: float = Field(..., gt=0, description="Payment amount in EUR")
    payment_method: PaymentMethod = Field(default=PaymentMethod.MANUAL, description="Method of payment")
    
    # Related orders
    related_orders: List[Link[CoffeeOrder]] = Field(default_factory=list, description="Orders this payment covers")
    
    created_at: datetime = Field(default_factory=datetime.now, description="When payment was completed")
    description: Optional[str] = Field(None, description="Payment description")
    
    class Settings:
        name = "payments"


# TODO: check
class UserDebt(Document):
    """Tracks debt between users for coffee orders."""

    debtor: Link[TelegramUser] = Field(..., description="User who owes money")
    creditor: Link[TelegramUser] = Field(..., description="User who is owed money")
    total_amount: float = Field(..., ge=0, description="Total debt amount")
    coffee_card: Link[CoffeeCard] = Field(..., description="Card the debt is related to")
    
    # Track individual orders that contribute to this debt
    orders: List[Link[CoffeeOrder]] = Field(default_factory=list)
    
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    is_settled: bool = Field(default=False, description="Whether debt is fully paid")
    
    class Settings:
        name = "user_debts"
        
    @classmethod
    async def get_user_debts(cls, user_id: int) -> Sequence["UserDebt"]:
        """Get all debts for a specific user (as debtor)."""
        # Find the user first, then query by the Link reference
        # Try FullUser first, then TelegramUser
        # Import here to avoid circular imports
        user = await FullUser.find_one(FullUser.user_id == user_id)
        if not user:
            user = await TelegramUser.find_one(TelegramUser.user_id == user_id)
        if not user:
            return []
        return await cls.find(cls.debtor == user, fetch_links=True).to_list()
    
    @classmethod
    async def get_user_credits(cls, user_id: int) -> Sequence["UserDebt"]:
        """Get all amounts owed to a specific user (as creditor)."""
        # Find the user first, then query by the Link reference
        # Try FullUser first, then TelegramUser
        # Import here to avoid circular imports
        user = await FullUser.find_one(FullUser.user_id == user_id)
        if not user:
            user = await TelegramUser.find_one(TelegramUser.user_id == user_id)
        if not user:
            return []
        return await cls.find(cls.creditor == user, fetch_links=True).to_list()
