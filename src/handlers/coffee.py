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
    CoffeeSession, PaymentMethod
)

from ..dependencies.dependencies import repo

from ..bot.group_state_helpers import initialize_group_state_from_db
from ..models.beanie_models import TelegramUser, FullUser
from ..exceptions.coffee_exceptions import (
    InvalidCoffeeCountError, InsufficientCoffeeError, SessionNotActiveError,
    CoffeeCardNotFoundError, InsufficientCoffeeCardCapacityError, UserNotFoundError
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

@repo 
async def create_coffee_card(
    repo: "BaseRepository",
    name: str,
    total_coffees: int,
    cost_per_coffee: float,
    purchaser_id: int
) -> CoffeeCard:
    """Create a new coffee card."""
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

    await card.insert()
    log_coffee_card_created(name, total_coffees, purchaser_id, cost_per_coffee)
    return card


async def get_active_coffee_cards() -> List[CoffeeCard]:
    """Get all active coffee cards."""
    return await CoffeeCard.find(CoffeeCard.is_active == True).to_list()


async def get_user_coffee_cards(user_id: int) -> List[CoffeeCard]:
    """Get all coffee cards purchased by a user."""
    # Try FullUser first, then TelegramUser
    user = await FullUser.find_one(FullUser.user_id == user_id)
    if not user:
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
    
    log_coffee_order_created(str(order.id), consumer_id, initiator_id, quantity, card.name)

    # Update card
    card.remaining_coffees -= quantity
    await card.save()

    # Create/update debt if consumer != card purchaser
    # Fetch/resolve the purchaser link to access its fields
    await card.fetch_link("purchaser")

    if consumer.user_id != card.purchaser.user_id:  # type: ignore
        debt_amount = float(quantity) * card.cost_per_coffee
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
    amount: float
) -> UserDebt:
    """Create or update debt between users."""

    # Get user documents first - try FullUser first, then TelegramUser
    debtor = await FullUser.find_one(FullUser.user_id == debtor_id)
    if not debtor:
        debtor = await TelegramUser.find_one(TelegramUser.user_id == debtor_id)
    
    creditor = await FullUser.find_one(FullUser.user_id == creditor_id)
    if not creditor:
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
        log_debt_created(debtor_id, creditor_id, amount, coffee_card.name)
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
    amount: float,
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
            remaining_amount = 0.0
        
        debt.updated_at = datetime.now()
        await debt.save()
        
        
# Session managment

async def get_active_session() -> Optional[CoffeeSession]:
    """Get the currently active coffee session."""
    return await CoffeeSession.find_one(CoffeeSession.is_active == True)

async def get_active_session_for_user(user_id: int) -> Optional[CoffeeSession]:
    """Get the active session initiated by a user."""
    # Try FullUser first, then TelegramUser
    user = await FullUser.find_one(FullUser.user_id == user_id)
    if not user:
        user = await TelegramUser.find_one(TelegramUser.user_id == user_id)
    if not user:
        return None
    
    return await CoffeeSession.find_one(
        CoffeeSession.initiator == user,
        CoffeeSession.is_active == True
    )


# TODO: check if it makes sense to still use group_members
async def update_session_coffee_counts(
    session: CoffeeSession,
    group_members: Dict[str, int]
) -> None:
    """Update coffee counts in the session's group state."""
    
    if not session.is_active:
        raise SessionNotActiveError()
    
    try:
        await session.validate_coffee_counts(group_members)
    except InvalidCoffeeCountError:
        # Re-raise with more context if needed
        raise
    except InsufficientCoffeeError as e:
        # The exception already has all the details we need
        await handle_insufficient_coffee_capacity(session, e.requested, e.available)
        raise e
    
    # Update the group state members directly
    session.group_state.members = group_members
    await session.save()
    

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
                user = await FullUser.find_one(FullUser.user_id == user_id)
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


async def get_session_summary(session: CoffeeSession) -> str:
    """
    Generate a human-readable summary of the session.
    
    Args:
        session: The coffee session to summarize
        
    Returns:
        str: Formatted session summary
    """
    await session.fetch_link(session.participants)
    await session.fetch_link(session.coffee_cards)
    
    total_coffees = await session.get_total_coffees()
    available_coffees = await session.get_available_coffees()
    
    summary = f"☕ **Coffee Session Summary**\n"
    summary += f"Session ID: `{session.id}`\n"
    summary += f"Participants: {len(session.participants)}\n"
    summary += f"Total Orders: {total_coffees} coffees\n"
    summary += f"Available: {available_coffees} coffees\n"
    
    if session.coffee_cards:
        summary += f"Cards: {len(session.coffee_cards)} card(s) available\n"
    
    if session.coffee_counts:
        summary += f"\n**Orders:**\n"
        for user_id, count in session.coffee_counts.items():
            user = await FullUser.find_one(FullUser.user_id == user_id)
            name = user.display_name if user and hasattr(user, 'display_name') else f"User {user_id}"
            summary += f"• {name}: {count}\n"
    
    return summary


async def complete_session_and_create_orders(session_id: str) -> CoffeeSession:
    """
    Complete a coffee session and process orders.
    
    Args:
        session_id: The ID of the session to complete
        
    Returns:
        The completed CoffeeSession
    """
    # Find the session
    session = await CoffeeSession.get(session_id)
    if session is None:
        raise ValueError(f"Session {session_id} not found")
        
    if not session.is_active:
        raise ValueError(f"Session {session_id} is not active")
    
    # total_coffees = await session.get_total_coffees()
    
    # # Notify all participants who have made orders
    # for participant_user_id in session.coffee_counts.keys():
    #     await self.api.message_manager.send_text(
    #         participant_user_id,
    #         f"✅ **Session Completed!**\n"
    #         f"Total: {total_coffees} coffees\n"
    #         f"Session ID: `{completed_session.id}`",
    #         True, True
    #     )
    
    # Mark session as completed
    session.is_active = False
    session.completed_date = datetime.now()
    await session.save()
    
    # TODO: Process actual coffee orders here
    # This would involve:
    # 1. Creating CoffeeOrder objects
    # 2. Updating coffee card counts
    # 3. Processing payments/debts
    
    print(f"✅ [SESSION] Completed session {session.id}")
    return session


async def cancel_session(session_id: str) -> None:
    """
    Cancel an active coffee session.
    
    Args:
        session_id: The ID of the session to cancel
    """
    # Find the session
    session = await CoffeeSession.get(session_id)
    if session is None:
        raise ValueError(f"Session {session_id} not found")
        
    if not session.is_active:
        raise ValueError(f"Session {session_id} is not active")
    
    session.is_active = False
    await session.save()
    
    print(f"❌ [SESSION] Cancelled session {session.id}")


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
        # Try FullUser first, then TelegramUser
        user = await FullUser.find_one(FullUser.user_id == user_id)
        if not user:
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


# async def process_telegram_keyboard_response(
#     initiator_id: int,
#     coffee_card_id: str,
#     keyboard_responses: Dict[str, int]  # username -> coffee_count from keyboard
# ) -> Dict[str, Any]:
#     """
#     Process responses from a Telegram group keyboard and create a session with orders.
#     This is the main function you'd call from your Telethon handler.
    
#     Args:
#         initiator_id: The user who initiated the coffee session
#         coffee_card_id: The coffee card to use
#         keyboard_responses: Dictionary of {username: coffee_count} from keyboard responses
        
#     Returns:
#         Dictionary with session info and created orders
        
#     Raises:
#         UserNotFoundError: If initiator or participants not found
#         InvalidCoffeeCountError: If coffee counts are invalid
#         InsufficientCoffeeError: If not enough coffees available
#     """
    
#     # First, find all users by their usernames/names
#     telegram_users = []
#     user_coffee_counts = {}  # user_id -> coffee_count
#     unknown_users = []
    
#     for username, coffee_count in keyboard_responses.items():
#         if coffee_count > 0:
#             # Try to find user by username first, then by first_name
#             user = await TelegramUser.find_one(
#                 {"$or": [
#                     {"username": username}, 
#                     {"first_name": username}
#                 ]}
#             )
#             if user:
#                 telegram_users.append(user)
#                 user_coffee_counts[user.user_id] = coffee_count
#             else:
#                 unknown_users.append(username)
#                 log_unexpected_error("user_lookup", f"User {username} not found in database during session processing")
    
#     if not telegram_users:
#         raise UserNotFoundError(message="No valid users found from keyboard responses")
    
#     # Create the session
#     session = await start_coffee_session(
#         initiator_id=initiator_id,
#         coffee_card_ids=[coffee_card_id]
#     )
    
#     # Add participants to the session (the users found from keyboard responses)
#     for user in telegram_users:
#         if user not in session.participants:
#             session.participants.append(user)
    
#     # Update coffee counts in the session
#     await update_session_coffee_counts(session, user_coffee_counts)
    
#     # Calculate totals for response
#     total_coffees = sum(coffee_count for coffee_count in keyboard_responses.values() if coffee_count > 0)
#     total_cost = await session.get_total_cost()
    
#     return {
#         "session": session,
#         "session_id": str(session.id),
#         "total_coffees": total_coffees,
#         "total_cost": float(total_cost),
#         "participants": len(telegram_users),
#         "participant_details": [
#             {"username": user.username or user.first_name, "user_id": user.user_id, "coffees": user_coffee_counts.get(user.user_id, 0)}
#             for user in telegram_users
#         ],
#         "unknown_users": unknown_users,
#         "success": True
#     }


async def complete_telegram_coffee_session(session_id: str) -> Dict[str, Any]:
    """
    Complete a coffee session by creating individual orders and updating debt tracking.
    This function bridges Telegram UI completion with domain business logic.
    
    Args:
        session_id: The session ID to complete
        
    Returns:
        Dictionary with completion results including created orders and debt updates
    """
    # TODO: Implement session completion logic
    # This would involve:
    # 1. Getting the session
    # 2. Creating individual CoffeeOrder records for each participant
    # 3. Updating coffee card remaining counts
    # 4. Creating/updating UserDebt records
    # 5. Marking session as inactive
    
    return {
        "success": True,
        "message": "Session completion not yet implemented",
        "session_id": session_id
    }