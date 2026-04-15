"""
Application service for placing coffee orders.
Centralizes cross-aggregate logic: CoffeeOrder, CoffeeCard, CoffeeSession, ConsumerStats.
"""
from typing import List, Optional

from ..models.coffee_models import CoffeeCard, CoffeeOrder, CoffeeSession, ConsumerStats
from ..models.beanie_models import TelegramUser, PassiveUser
from ..exceptions.coffee_exceptions import InsufficientCoffeeError


async def place_order(
    *,
    initiator: TelegramUser,
    consumer: PassiveUser,
    cards: List[CoffeeCard],
    quantity: int,
    session: Optional[CoffeeSession] = None,
    enforce_capacity: bool = False,
) -> List[CoffeeOrder]:
    """
    Create order document(s) and update related card/session state.

    Args:
        initiator: User who initiated the order
        consumer: User who consumed the coffees
        cards: Cards to use (ordered by preference)
        quantity: Total coffees in this order
        session: Optional session linkage
        enforce_capacity: When True, validates available coffees across provided cards
                          and raises InsufficientCoffeeError if not enough.
                          Use this when a session is already submitted.

    Returns:
        The created CoffeeOrder documents (one per card used)
    """
    # Optional capacity check across provided cards
    if enforce_capacity:
        total_available = sum(max(0, c.remaining_coffees) for c in cards)
        if quantity > total_available:
            # Keep the domain-wide exception used elsewhere (InsufficientCoffeeError)
            raise InsufficientCoffeeError(requested=quantity, available=total_available)

    # Deduct coffees and create one order per card.
    remaining_to_deduct = int(quantity)
    created_orders: List[CoffeeOrder] = []

    for card in cards:
        if remaining_to_deduct <= 0:
            break

        available_here = max(0, int(card.remaining_coffees))
        if available_here <= 0:
            continue

        deduct_from_this_card = min(remaining_to_deduct, available_here)
        if deduct_from_this_card <= 0:
            continue

        card.remaining_coffees -= deduct_from_this_card
        remaining_to_deduct -= deduct_from_this_card

        order = CoffeeOrder(
            coffee_cards=[card],  # type: ignore[list-item]
            initiator=initiator,
            consumer=consumer,
            quantity=deduct_from_this_card,
            session=session,
        )
        await order.insert()
        created_orders.append(order)

    # Update each card's relationships and consumer stats
    consumer_key = consumer.stable_id  # Use stable_id as the key

    # Group created orders by their single card.
    orders_by_card_id: dict[str, List[CoffeeOrder]] = {}
    for order in created_orders:
        linked_card = order.coffee_cards[0]
        if not isinstance(linked_card, CoffeeCard) or linked_card.id is None:
            continue
        orders_by_card_id.setdefault(str(linked_card.id), []).append(order)

    for card in cards:
        if card.id is None:
            continue

        card_orders = orders_by_card_id.get(str(card.id), [])
        if not card_orders:
            continue

        for order in card_orders:
            if order not in card.orders:
                card.orders.append(order)  # type: ignore[attr-defined]

            coffees_from_this_card = int(order.quantity)
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
                    stats.display_name = consumer.display_name
                    stats.last_order_date = order.order_date

        await card.save()

    # Link to session if applicable
    if session:
        for order in created_orders:
            if order not in session.orders:
                session.orders.append(order)  # type: ignore[attr-defined]

        # Add session to cards (only once per card per session)
        for card in cards:
            if session not in card.sessions:
                card.sessions.append(session)  # type: ignore[attr-defined]
                await card.save()

        await session.save()
    
    # Reset inactive_card_count for the consumer since they just ordered
    # Also unarchive and re-enable them if necessary
    if consumer.inactive_card_count > 0 or consumer.is_archived or consumer.is_disabled:
        consumer.inactive_card_count = 0
        consumer.is_archived = False
        consumer.is_disabled = False
        await consumer.save()
        print(f"✅ Reset inactive counter for {consumer.display_name} (placed order)")

    return created_orders

