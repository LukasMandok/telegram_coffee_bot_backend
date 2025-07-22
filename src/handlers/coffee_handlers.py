from typing import List, Optional, Dict, Any
from decimal import Decimal
from datetime import datetime

from ..database.base_repo import BaseRepository
from ..models.coffee_models import (
    CoffeeCard, CoffeeOrder, Payment, UserDebt, 
    CoffeeSession, PaymentMethod
)
from ..models.beanie_models import TelegramUser
from ..exceptions.coffee_exceptions import (
    InvalidCoffeeCountError, InsufficientCoffeeError, SessionNotActiveError,
    CoffeeCardNotFoundError, InsufficientCoffeeCardCapacityError, UserNotFoundError
)

from ..utils.typing_utils import Link

async def create_coffee_card(
    repo: BaseRepository,
    name: str,
    total_coffees: int,
    cost_per_coffee: Decimal,
    purchaser_id: int
) -> CoffeeCard:
    """Create a new coffee card."""
    purchaser = await repo.find_user_by_id(purchaser_id)
    if not purchaser:
        raise ValueError("Purchaser not found")

    total_cost = Decimal(total_coffees) * cost_per_coffee

    card = CoffeeCard(
        name=name,
        total_coffees=total_coffees,
        remaining_coffees=total_coffees,
        cost_per_coffee=cost_per_coffee,
        total_cost=total_cost,
        purchaser=purchaser
    )

    await card.insert()
    return card


async def get_active_coffee_cards() -> List[CoffeeCard]:
    """Get all active coffee cards."""
    return await CoffeeCard.find(CoffeeCard.is_active == True).to_list()


async def get_user_coffee_cards(user_id: int) -> List[CoffeeCard]:
    """Get all coffee cards purchased by a user."""
    user = await TelegramUser.find_one(TelegramUser.user_id == user_id)
    if not user:
        return []
    return await CoffeeCard.find(CoffeeCard.purchaser == user, 
                                 fetch_links=True).to_list()


# TODO: check
async def create_coffee_order(
    coffee_card_id: str,
    initiator_id: int,
    consumer_id: int,
    quantity: int
) -> CoffeeOrder:
    """Create a coffee order and update card/debt tracking."""

    # Get the coffee card and consumer
    card = await CoffeeCard.get(coffee_card_id)
    if not card:
        raise CoffeeCardNotFoundError(coffee_card_id)

    initiator = await TelegramUser.get(initiator_id)
    if not initiator: 
        raise UserNotFoundError(user_id=initiator_id)

    consumer = await TelegramUser.get(consumer_id)
    if not consumer:
        raise UserNotFoundError(user_id=consumer_id)

    # Check if enough coffees are available
    if card.remaining_coffees < quantity:
        raise InsufficientCoffeeCardCapacityError(
            requested=quantity,
            available=card.remaining_coffees,
            card_name=card.name
        )

    # Create the order
    order = CoffeeOrder(
        coffee_card=card,
        initiator=initiator,
        consumer=consumer,
        quantity=quantity,
        session=None  # Explicitly set as None for individual orders
    )
    await order.insert()

    # Update card
    card.remaining_coffees -= quantity
    await card.save()

    # Create/update debt if consumer != card purchaser
    # Fetch/resolve the purchaser link to access its fields
    await card.fetch_link("purchaser")

    if consumer.user_id != card.purchaser.user_id:  # type: ignore
        debt_amount = Decimal(quantity) * card.cost_per_coffee
        await create_or_update_debt(
            debtor_id=consumer.user_id,
            creditor_id=card.purchaser.user_id,  # type: ignore
            coffee_card=card,
            order=order,
            amount=debt_amount
        )

    # Fetch links before returning so they're accessible in the router
    await order.fetch_all_links()
    return order


# TODO: check
async def create_or_update_debt(
    debtor_id: int,
    creditor_id: int,
    coffee_card: CoffeeCard,
    order: CoffeeOrder,
    amount: Decimal
) -> UserDebt:
    """Create or update debt between users."""

    # Get user documents first
    debtor = await TelegramUser.find_one(TelegramUser.user_id == debtor_id)
    creditor = await TelegramUser.find_one(TelegramUser.user_id == creditor_id)

    if not debtor or not creditor:
        raise ValueError("Debtor or creditor not found")

    # Try to find existing debt using document references
    existing_debt = await UserDebt.find_one(
        UserDebt.debtor == debtor,
        UserDebt.creditor == creditor,
        UserDebt.coffee_card == coffee_card,
        UserDebt.is_settled == False
    )

    if existing_debt:
        # Update existing debt
        existing_debt.total_amount += amount
        existing_debt.orders.append(order)
        existing_debt.updated_at = datetime.now()
        await existing_debt.save()
        return existing_debt
    else:
        # Create new debt - Beanie automatically converts documents to Links
        debt = UserDebt(
            debtor=debtor,
            creditor=creditor, 
            total_amount=amount,
            coffee_card=coffee_card,
            orders=[order]
        )
        await debt.insert()
        return debt


# TODO: check
async def get_user_debts(user_id: int) -> Dict[str, List[UserDebt]]:
    """Get all debts for a user (what they owe and what's owed to them)."""
    debts_owed = await UserDebt.get_user_debts(user_id)
    debts_owed_to_me = await UserDebt.get_user_credits(user_id)

    return {
        "debts_i_owe": list(debts_owed),
        "debts_owed_to_me": list(debts_owed_to_me)
    }


# TODO: check
async def create_payment(
    payer_id: int,
    recipient_id: int,
    amount: Decimal,
    payment_method: PaymentMethod = PaymentMethod.MANUAL,
    related_order_ids: Optional[List[str]] = None,
    description: Optional[str] = None
) -> Payment:
    """Create a payment record (payment is considered completed when created)."""

    payer = await TelegramUser.get(payer_id)
    recipient = await TelegramUser.get(recipient_id)

    if not payer or not recipient:
        raise ValueError("Payer or recipient not found")

    related_orders = []
    if related_order_ids:
        for order_id in related_order_ids:
            order = await CoffeeOrder.get(order_id)
            if order:
                related_orders.append(order)

    payment = Payment(
        payer=payer,
        recipient=recipient,
        amount=amount,
        payment_method=payment_method,
        related_orders=related_orders,
        description=description
    )

    await payment.insert()

    # Update related debts
    await settle_debts_for_payment(payment)

    return payment



# async def complete_payment(payment_id: str, paypal_payment_id: Optional[str] = None) -> Payment:
#     """Mark a payment as completed."""
#     payment = await Payment.get(payment_id)
#     if not payment:
#         raise ValueError("Payment not found")
    
#     payment.payment_status = PaymentStatus.COMPLETED
#     payment.completed_at = datetime.now()
    
#     if paypal_payment_id:
#         payment.paypal_payment_id = paypal_payment_id
    
#     await payment.save()
    
#     # Update related debts
#     await settle_debts_for_payment(payment)
    
#     return payment


# TODO: check
async def settle_debts_for_payment(payment: Payment) -> None:
    """Settle debts when a payment is completed."""
    
    # Fetch the payment links to get actual user objects
    await payment.fetch_link("payer")
    await payment.fetch_link("recipient")
    
    # Find relevant debts
    debts = await UserDebt.find(
        UserDebt.debtor == payment.payer,
        UserDebt.creditor == payment.recipient,
        UserDebt.is_settled == False
    ).to_list()
    
    remaining_amount = payment.amount
    
    for debt in debts:
        if remaining_amount <= 0:
            break
            
        if debt.total_amount <= remaining_amount:
            # Fully settle this debt
            debt.is_settled = True
            remaining_amount -= debt.total_amount
        else:
            # Partially settle this debt
            debt.total_amount -= remaining_amount
            remaining_amount = Decimal('0')
        
        debt.updated_at = datetime.now()
        await debt.save()
        
        
# Session managment

async def get_active_session() -> Optional[CoffeeSession]:
    """Get the currently active coffee session."""
    return await CoffeeSession.find_one(CoffeeSession.is_active == True)

async def get_active_session_for_user(user_id: int) -> Optional[CoffeeSession]:
    """Get the active session initiated by a user."""
    user = await TelegramUser.find_one(TelegramUser.user_id == user_id)
    if not user:
        return None
    
    return await CoffeeSession.find_one(
        CoffeeSession.initiator == user,
        CoffeeSession.is_active == True
    )



async def start_coffee_session(
    initiator_id: int,
    coffee_card_ids: List[str],
) -> CoffeeSession:
    """Start a new coffee ordering session."""
    
    initiator = await TelegramUser.get(initiator_id)
    cards = await CoffeeCard.find({"_id": {"$in": coffee_card_ids}}).to_list()

    if not initiator or not cards:
        raise ValueError("Initiator or coffee card not found")
    
    # QUESTION: return active session or raise error
    session = await get_active_session()
    if session:
        print(f"Active session already exists: {session.id}. Returning existing session.")
        return session

    for card in cards:
        if card.remaining_coffees < 1:
            print(f"No coffees available on card {card.id}.")
            cards.remove(card)
        # else:
        #     await card.fetch_all_links()

    if not cards:
        raise ValueError("No valid coffee cards available.")
    
    session = CoffeeSession(
        initiator=initiator,
        coffee_cards=cards # type: ignore
    )
    
    await session.insert()
    return session


async def add_participant_to_session(
    session: CoffeeSession,
    user: TelegramUser,
) -> None:
    """Add a participant to an existing coffee session."""
    if not session.is_active:
        raise SessionNotActiveError()

    if user in session.participants:
        print(f"User {user.user_id} is already a participant in this session.")
        return

    session.participants.append(user)
    await session.save()
    
    
async def update_session_coffee_counts(
    session: CoffeeSession,
    coffee_counts: Dict[int, int]
) -> None:
    """Update coffee counts in the user dictionary."""
    
    if not session.is_active:
        raise SessionNotActiveError()
    
    try:
        await session.validate_coffee_counts(coffee_counts)
    except InvalidCoffeeCountError:
        # Re-raise with more context if needed
        raise
    except InsufficientCoffeeError as e:
        # The exception already has all the details we need
        await handle_insufficient_coffee_capacity(session, e.requested, e.available)
        raise e
    
    await session.set({"coffee_counts": coffee_counts})
    

async def complete_coffee_session(session_id: str) -> List[CoffeeOrder]:
    """Complete a coffee session by creating individual orders."""
    
    session = await CoffeeSession.get(session_id)
    if not session:
        raise ValueError("Session not found")
    
    if not session.is_active:
        raise ValueError("Session is not active")
    
    orders = []
    
    return orders


async def get_coffee_statistics() -> Dict:
    """Get overall coffee consumption statistics."""
    
    # Total cards
    total_cards = await CoffeeCard.count()
    active_cards = await CoffeeCard.find(CoffeeCard.is_active == True).count()
    
    # Total orders
    total_orders = await CoffeeOrder.count()
    
    # Total payments
    total_payments = await Payment.count()
    
    # Outstanding debts
    outstanding_debts = await UserDebt.find(UserDebt.is_settled == False).to_list()
    total_outstanding = sum(debt.total_amount for debt in outstanding_debts)
    
    return {
        "cards": {
            "total": total_cards,
            "active": active_cards
        },
        "orders": {
            "total": total_orders
        },
        "payments": {
            "total": total_payments
        },
        "debts": {
            "count": len(outstanding_debts),
            "total_amount": float(total_outstanding)
        }
    }

async def handle_insufficient_coffee_capacity(session: CoffeeSession, requested: int, available: int) -> None:
    pass