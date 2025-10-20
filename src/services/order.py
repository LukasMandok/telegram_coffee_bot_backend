"""
Application service for placing coffee orders.
Centralizes cross-aggregate logic: CoffeeOrder, CoffeeCard, CoffeeSession, ConsumerStats.
"""
from typing import List, Optional, Dict

from ..models.coffee_models import CoffeeCard, CoffeeOrder, CoffeeSession, ConsumerStats
from ..models.beanie_models import TelegramUser, PassiveUser
from ..exceptions.coffee_exceptions import InsufficientCoffeeError


async def place_order(
    *,
    initiator: TelegramUser,
    consumer: PassiveUser,
    cards: List[CoffeeCard],
    quantity: int,
    from_session: bool,
    session: Optional[CoffeeSession] = None,
    enforce_capacity: bool = False,
) -> CoffeeOrder:
    """
    Create an order and update related card/session state.

    Args:
        initiator: User who initiated the order
        consumer: User who consumed the coffees
        cards: Cards to use (ordered by preference)
        quantity: Total coffees in this order
        from_session: Whether this order comes from a session
        session: Optional session linkage
        enforce_capacity: When True, validates available coffees across provided cards
                          and raises InsufficientCoffeeError if not enough.
                          Use this when a session is already submitted.

    Returns:
        The created CoffeeOrder document
    """
    # Optional capacity check across provided cards
    if enforce_capacity:
        total_available = sum(max(0, c.remaining_coffees) for c in cards)
        if quantity > total_available:
            # Keep the domain-wide exception used elsewhere (InsufficientCoffeeError)
            raise InsufficientCoffeeError(requested=quantity, available=total_available)

    # Deduct coffees from cards and track per-card amounts
    remaining_to_deduct = quantity
    card_deductions: Dict[str, int] = {}

    for card in cards:
        if remaining_to_deduct == 0:
            break

        deduct_from_this_card = min(remaining_to_deduct, max(0, card.remaining_coffees))
        card.remaining_coffees -= deduct_from_this_card
        card_deductions[str(card.id)] = deduct_from_this_card
        remaining_to_deduct -= deduct_from_this_card

    # Create the order
    order = CoffeeOrder(
        coffee_cards=cards,  # type: ignore[list-item]
        initiator=initiator,
        consumer=consumer,
        quantity=quantity,
        from_session=from_session,
    )
    await order.insert()

    # Update each card's relationships and consumer stats
    consumer_key = consumer.stable_id  # Use stable_id as the key

    for card in cards:
        # Add order to card
        if order not in card.orders:
            card.orders.append(order)  # type: ignore[attr-defined]

        # Update consumer stats (only for cards that actually contributed)
        coffees_from_this_card = card_deductions.get(str(card.id), 0)
        if coffees_from_this_card > 0:
            if consumer_key not in card.consumer_stats:
                card.consumer_stats[consumer_key] = ConsumerStats(
                    user_id=consumer.stable_id,
                    display_name=consumer.display_name,
                    total_coffees=coffees_from_this_card,
                    last_order_date=order.order_date,
                )
            else:
                stats = card.consumer_stats[consumer_key]
                stats.total_coffees += coffees_from_this_card
                stats.display_name = consumer.display_name  # Keep display name up-to-date
                stats.last_order_date = order.order_date

        await card.save()

    # Link to session if applicable
    if session:
        if order not in session.orders:
            session.orders.append(order)  # type: ignore[attr-defined]

        # Add session to cards (only once per card per session)
        for card in cards:
            if session not in card.sessions:
                card.sessions.append(session)  # type: ignore[attr-defined]
                await card.save()
    
    # Reset inactive_card_count for the consumer since they just ordered
    # Also unarchive and re-enable them if necessary
    if consumer.inactive_card_count > 0 or consumer.is_archived or consumer.is_disabled:
        consumer.inactive_card_count = 0
        consumer.is_archived = False
        consumer.is_disabled = False
        await consumer.save()
        print(f"✅ Reset inactive counter for {consumer.display_name} (placed order)")

    return order

