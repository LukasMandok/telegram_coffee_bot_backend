"""
Telegram bot commands and Telegram-specific business logic.

This module contains command handlers and orchestration functions that combine
Telegram-specific operations with domain business logic.
"""

from typing import Dict, Any, List
from ..models.beanie_models import TelegramUser
from ..handlers.coffee_handlers import (
    start_coffee_session,
    update_session_coffee_counts
)
from ..exceptions.coffee_exceptions import (
    InvalidCoffeeCountError, 
    InsufficientCoffeeError, 
    SessionNotActiveError,
    UserNotFoundError
)


async def process_telegram_keyboard_response(
    initiator_id: int,
    coffee_card_id: str,
    keyboard_responses: Dict[str, int]  # username -> coffee_count from keyboard
) -> Dict[str, Any]:
    """
    Process responses from a Telegram group keyboard and create a session with orders.
    This is the main function you'd call from your Telethon handler.
    
    Args:
        initiator_id: The user who initiated the coffee session
        coffee_card_id: The coffee card to use
        keyboard_responses: Dictionary of {username: coffee_count} from keyboard responses
        
    Returns:
        Dictionary with session info and created orders
        
    Raises:
        UserNotFoundError: If initiator or participants not found
        InvalidCoffeeCountError: If coffee counts are invalid
        InsufficientCoffeeError: If not enough coffees available
    """
    
    # First, find all users by their usernames/names
    telegram_users = []
    user_coffee_counts = {}  # user_id -> coffee_count
    unknown_users = []
    
    for username, coffee_count in keyboard_responses.items():
        if coffee_count > 0:
            # Try to find user by username first, then by first_name
            user = await TelegramUser.find_one(
                {"$or": [
                    {"username": username}, 
                    {"first_name": username}
                ]}
            )
            if user:
                telegram_users.append(user)
                user_coffee_counts[user.user_id] = coffee_count
            else:
                unknown_users.append(username)
                print(f"Warning: User {username} not found in database")
    
    if not telegram_users:
        raise UserNotFoundError(message="No valid users found from keyboard responses")
    
    # Create the session
    session = await start_coffee_session(
        initiator_id=initiator_id,
        coffee_card_ids=[coffee_card_id]
    )
    
    # Add participants to the session (the users found from keyboard responses)
    for user in telegram_users:
        if user not in session.participants:
            session.participants.append(user)
    
    # Update coffee counts in the session
    await update_session_coffee_counts(session, user_coffee_counts)
    
    # Calculate totals for response
    total_coffees = sum(coffee_count for coffee_count in keyboard_responses.values() if coffee_count > 0)
    total_cost = await session.get_total_cost()
    
    return {
        "session": session,
        "session_id": str(session.id),
        "total_coffees": total_coffees,
        "total_cost": float(total_cost),
        "participants": len(telegram_users),
        "participant_details": [
            {"username": user.username or user.first_name, "user_id": user.user_id, "coffees": user_coffee_counts.get(user.user_id, 0)}
            for user in telegram_users
        ],
        "unknown_users": unknown_users,
        "success": True
    }


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