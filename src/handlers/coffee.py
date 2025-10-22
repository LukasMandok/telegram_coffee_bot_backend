"""
Coffee business logic handlers.

This module contains all the business logic for coffee operations including
card management, order processing, session handling, and debt tracking.
"""

from typing import List, Optional, Dict, Any, TYPE_CHECKING, Tuple
from datetime import datetime

# Runtime imports - actually used in code
from ..models.coffee_models import (
    CoffeeCard, CoffeeOrder, Payment, UserDebt, 
    CoffeeSession, PaymentMethod, ConsumerStats
)

from ..dependencies.dependencies import repo
from ..services.order import place_order

from ..bot.group_state_helpers import initialize_group_state_from_db
from ..models.beanie_models import TelegramUser
from ..exceptions.coffee_exceptions import (
    InvalidCoffeeCountError, InsufficientCoffeeError, SessionNotActiveError,
    CoffeeCardNotFoundError, UserNotFoundError
)
from ..exceptions.coffee_exceptions import (
    InvalidCoffeeCountError, InsufficientCoffeeError, SessionNotActiveError,
    CoffeeCardNotFoundError, UserNotFoundError
)
from ..common.log import (
    log_coffee_card_created, log_coffee_card_activated, log_coffee_card_deactivated, 
    log_coffee_card_depleted, log_coffee_order_created, log_coffee_order_failed,
    log_individual_coffee_order, log_coffee_session_started, log_coffee_session_participant_added,
    log_coffee_session_updated, log_coffee_session_completed, log_coffee_session_cancelled,
    log_debt_created, log_payment_recorded, log_unexpected_error
)

# Type-only imports - only needed for type annotations
if TYPE_CHECKING:
    from ..database.base_repo import BaseRepository




# TODO: check
async def create_coffee_order(
    coffee_card_id: str,
    initiator_id: int,
    consumer_id: int,
    quantity: int
) -> CoffeeOrder:
    """Create a coffee order and update card/debt tracking."""

    # Get the coffee card and validate
    card = await CoffeeCard.get(coffee_card_id)
    if not card:
        raise CoffeeCardNotFoundError(coffee_card_id)

    # Look up users
    initiator = await TelegramUser.find_one(TelegramUser.user_id == initiator_id)
    if not initiator: 
        raise UserNotFoundError(user_id=initiator_id)

    consumer = await TelegramUser.find_one(TelegramUser.user_id == consumer_id)
    if not consumer:
        raise UserNotFoundError(user_id=consumer_id)

    # Check availability
    if card.remaining_coffees < quantity:
            raise InsufficientCoffeeError(
                requested=quantity,
                available=card.remaining_coffees
            )

    # Use the shared order creation utility
    order = await place_order(
        initiator=initiator,
        consumer=consumer,
        cards=[card],
        quantity=quantity,
        from_session=False,
        session=None,
        enforce_capacity=True
    )
    
    log_coffee_order_created(str(order.id), consumer_id, initiator_id, quantity, card.name)

    # Create/update debt if consumer != card purchaser
    await card.fetch_link("purchaser")

    # Debts are created/updated only when a coffee card is completed via DebtManager

    # Fetch links before returning so they're accessible in the router
    await order.fetch_all_links()
    return order

    # ... debts are handled by DebtManager on card completion


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
    amount: float,
    payment_method: PaymentMethod = PaymentMethod.MANUAL,
    related_order_ids: Optional[List[str]] = None,
    description: Optional[str] = None
) -> Payment:
    """Create a payment record (payment is considered completed when created)."""

    payer = await TelegramUser.find_one(TelegramUser.user_id == payer_id)
    recipient = await TelegramUser.find_one(TelegramUser.user_id == recipient_id)

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
    
    payer = payment.payer  # type: ignore
    recipient = payment.recipient  # type: ignore
    
    # Find relevant debts
    # Query using MongoDB $oid comparison for Link fields
    debts = await UserDebt.find(
        {"debtor.$id": payer.id, "creditor.$id": recipient.id},
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
            debt.settled_at = datetime.now()
        else:
            # Partially settle this debt
            debt.total_amount -= remaining_amount
            remaining_amount = 0.0
        
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



# I am not sure what this is supposed to be. 
async def broadcast_session_changes(session: CoffeeSession, api_instance=None) -> None:
    """
    Broadcast session changes to all participants.
    
    This function notifies all session participants about state changes
    so their UI can be updated in real-time.
    
    Args:
        session: The session with updated state
        api_instance: Optional TelethonAPI instance for sending messages
    """
    total_coffees = await session.get_total_coffees()
    
    if api_instance and hasattr(api_instance, 'message_manager') and session.coffee_counts:
        # Create summary message
        summary = f"🔄 **Session Update**\n"
        summary += f"Total orders: {total_coffees} coffees\n"
        
        if session.coffee_counts:
            summary += "**Current orders:**\n"
            for user_id, count in session.coffee_counts.items():
                user = await TelegramUser.find_one(TelegramUser.user_id == user_id)
                name = user.display_name if user and hasattr(user, 'display_name') else f"User {user_id}"
                if count > 0:
                    summary += f"• {name}: {count}\n"
        
        # Send update to all participants who have made orders
        for user_id in session.coffee_counts.keys():
            try:
                # Send update (non-blocking)
                await api_instance.message_manager.send_text(
                    user_id,
                    summary,
                    False,  # No parse mode
                    False   # Don't auto-delete
                )
            except Exception as e:
                print(f"Failed to broadcast to participant {user_id}: {e}")
    
    participant_count = len(session.coffee_counts) if session.coffee_counts else 0
    print(f"📡 [BROADCAST] Session {session.id} changes to {participant_count} participants ({total_coffees} total coffees)")



async def get_user_active_session(user_id: int) -> Optional[CoffeeSession]:
    """
    Get the active coffee session for a specific user.
    
    Args:
        user_id: Telegram user ID
        
    Returns:
        Active CoffeeSession if found, None otherwise
    """
    try:
        # Find session where user is a participant and is active
        user = await TelegramUser.find_one(TelegramUser.user_id == user_id)
        if not user:
            return None
            
        session = await CoffeeSession.find_one(
            CoffeeSession.participants == user,
            CoffeeSession.is_active == True
        )
        return session
    except Exception as e:
        print(f"Error getting user active session: {e}")
        return None