"""Coffee card conversations (MessageFlow-based).

Implements three entry points:
- `/new_card` -> create a new coffee card
- `/close_card` -> close the current (oldest active) coffee card
- `/cards` -> main menu: show active card status, create/close, list all cards with pagination

Design goals:
- Minimal state machine, message edits instead of new messages
- Numeric inputs parsed via IntegerParser/MoneyParser
- Validation feedback shown inline (like settings_flow enter_link state)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Dict, List, Optional, cast

from beanie.odm.fields import Link as BeanieLink

from ..message_flow import (
    ButtonCallback,
    MessageDefinition,
    MessageFlow,
    PaginationConfig,
    StateType,
    TextLengthValidator,
)
from ..message_flow_helpers import (
    CommonCallbacks,
    CommonFlowKeys,
    CommonStateIds,
    ExitStateBuilder,
    IntegerParser,
    MoneyParser,
    NavigationButtons,
    format_date,
)
from ...common.log import Logger
from ...config import app_config
from ...models.beanie_models import PassiveUser, TelegramUser
from ...models.coffee_models import CoffeeCard, CoffeeOrder, CoffeeSession


_logger = Logger("CardFlow")


DEFAULT_TOTAL_COFFEES = 200
DEFAULT_COST_PER_COFFEE = 0.8


STATE_MENU = CommonStateIds.MENU
STATE_CARDS_LIST = "cards_list"
STATE_CARD_DETAILS = "card_details"

STATE_CREATE_MAIN = "create_main"
STATE_CREATE_TOTAL = "create_total"
STATE_CREATE_PRICE = "create_price"
STATE_CREATE_CONFIRM = "create_confirm"
STATE_CREATE_EXECUTE = "create_execute"

STATE_CLOSE_CONFIRM = CommonStateIds.CLOSE_CONFIRM
STATE_CLOSE_EXECUTE = CommonStateIds.CLOSE_EXECUTE

CB_CLOSE_CURRENT = "close_current"
CB_SHOW_ALL_CARDS = "show_all_cards"
CB_SHOW_ACTIVE_CARD = "show_active_card"

KEY_TOTAL_COFFEES = "total_coffees"
KEY_COST_PER_COFFEE = "cost_per_coffee"

KEY_SELECTED_CARD_ID = "selected_card_id"
KEY_DETAILS_BACK_STATE = "details_back_state"


@dataclass(frozen=True)
class CardHistoryItem:
    text: str


async def _format_active_card_line(card: CoffeeCard) -> str:
    purchaser_name = "(unknown)"
    try:
        await card.fetch_link("purchaser")
        purchaser_name = card.purchaser.display_name  # type: ignore[attr-defined]
    except Exception:
        pass

    return (
        f"• **{card.name}** — {card.remaining_coffees} left\n"
        f"  Created: {format_date(card.created_at)}\n"
        f"  Purchaser: {purchaser_name}"
    )


# ============================================================================
# /cards MENU
# ============================================================================


async def build_card_menu_text(flow_state, api, user_id: int) -> str:
    active_cards = await api.coffee_card_manager.get_active_coffee_cards()

    if not active_cards:
        active_text = "No active coffee cards."
    else:
        active_text = "\n".join([await _format_active_card_line(card) for card in active_cards])

    return (
        "☕ **Coffee Cards**\n\n"
        "**Active Cards:**\n"
        f"{active_text}\n\n"
        "What would you like to do?"
    )


async def handle_card_menu_button(data: str, flow_state, api, user_id: int) -> Optional[str]:
    """Only handles the dynamic 'close_current' action; static actions route via state_id."""
    if data != CB_CLOSE_CURRENT:
        if data == CB_SHOW_ALL_CARDS:
            # Start at page 1 when opening from the menu.
            flow_state.pagination_state.pop(STATE_CARDS_LIST, None)
            return STATE_CARDS_LIST

        if data == CB_SHOW_ACTIVE_CARD:
            oldest = await api.coffee_card_manager.get_oldest_active_coffee_card()
            if not oldest or oldest.id is None:
                await api.message_manager.send_text(
                    user_id,
                    "❌ No active coffee cards found.",
                    vanish=True,
                    conv=True,
                    delete_after=3,
                )
                return STATE_MENU

            flow_state.set(KEY_SELECTED_CARD_ID, str(oldest.id))
            flow_state.set(KEY_DETAILS_BACK_STATE, STATE_MENU)
            return STATE_CARD_DETAILS

        return None

    oldest = await api.coffee_card_manager.get_oldest_active_coffee_card()
    if not oldest:
        await api.message_manager.send_text(
            user_id,
            "❌ No active coffee cards found.",
            vanish=True,
            conv=True,
            delete_after=3,
        )
        return STATE_MENU

    return STATE_CLOSE_CONFIRM


menu_buttons: List[List[ButtonCallback]] = [
    [
        ButtonCallback("➕ Create new Card", STATE_CREATE_MAIN),
        ButtonCallback("✅ Close current Card", CB_CLOSE_CURRENT),
    ],
    [
        ButtonCallback("🟢 Show Active Card", CB_SHOW_ACTIVE_CARD),
        ButtonCallback("📋 Show All Cards", CB_SHOW_ALL_CARDS),
    ],
    NavigationButtons.close(),
]

menu_defaults: dict[str, Any] = {
    CommonFlowKeys.AFTER_CANCEL: STATE_MENU,
}

menu_allowlist: List[str] = [STATE_CREATE_MAIN]


# ============================================================================
# ALL CARDS (PAGINATED)
# ============================================================================


async def list_all_cards(flow_state, api, user_id: int) -> List[CoffeeCard]:
    # Newest first.
    return await CoffeeCard.find(fetch_links=True).sort("-created_at").to_list()  # type: ignore[arg-type]


def build_card_number_button(card: CoffeeCard, index: int) -> ButtonCallback:
    # Prefer using the number from the card name (e.g. "Card 17" -> "17").
    match = re.search(r"(\d+)(?!.*\d)", card.name)
    number_text = match.group(1) if match else str(index + 1)
    card_id = str(card.id) if card.id is not None else ""
    return ButtonCallback(number_text, f"card_details:{card_id}")


async def handle_cards_list_button(data: str, flow_state, api, user_id: int) -> Optional[str]:
    if not data.startswith("card_details:"):
        return None

    _prefix, card_id = data.split(":", 1)
    if not card_id:
        await api.message_manager.send_text(
            user_id,
            "❌ Card not found.",
            vanish=True,
            conv=True,
            delete_after=3,
        )
        return None

    flow_state.set(KEY_SELECTED_CARD_ID, card_id)
    flow_state.set(KEY_DETAILS_BACK_STATE, STATE_CARDS_LIST)
    return STATE_CARD_DETAILS


async def build_card_details_text(flow_state, api, user_id: int) -> str:
    card_id = flow_state.get(KEY_SELECTED_CARD_ID)
    if not isinstance(card_id, str) or not card_id:
        return "❌ **Coffee Card Details**\n\nNo card selected."

    card = await CoffeeCard.get(card_id)
    if not card:
        return "❌ **Coffee Card Details**\n\nCard not found."

    purchaser_name = "(unknown)"
    try:
        await card.fetch_link("purchaser")
        purchaser: TelegramUser = card.purchaser  # type: ignore[assignment]
        purchaser_name = purchaser.display_name
    except Exception:
        pass

    status = "🟢 Active" if card.is_active else "⚪ Completed"
    completed_line = ""
    if not card.is_active and card.completed_at:
        completed_line = f"Completed: {format_date(card.completed_at)}\n"

    return (
        "🔎 **Coffee Card Details**\n\n"
        f"**{card.name}** — {status}\n"
        f"Remaining: {card.remaining_coffees}/{card.total_coffees}\n"
        f"Created: {format_date(card.created_at)}\n"
        f"{completed_line}"
        f"Cost per coffee: €{card.cost_per_coffee:.2f}\n"
        f"Total cost: €{card.total_cost:.2f}\n"
        f"Purchaser: {purchaser_name}\n\n"
        "**Order History:**"
    )


async def _fetch_order_people(order: CoffeeOrder) -> None:
    try:
        await order.fetch_link("consumer")
    except Exception as exc:
        if app_config.DEBUG_MODE:
            _logger.warning(
                f"Failed to fetch consumer link for order_id={order.id}",
                extra_tag="Cards",
                exc=exc,
            )

    # Some historical orders store consumer as a DBRef into `telegram_users`.
    # When the model expects a different collection, Beanie can leave the link unresolved.
    if isinstance(order.consumer, BeanieLink) and order.consumer.ref is not None:
        ref = order.consumer.ref
        if ref.collection == TelegramUser.Settings.name:
            resolved = await TelegramUser.get(ref.id)
            if resolved is not None:
                order.consumer = resolved  # type: ignore[assignment]
        elif ref.collection == PassiveUser.Settings.name:
            resolved = await PassiveUser.get(ref.id)
            if resolved is not None:
                order.consumer = resolved  # type: ignore[assignment]

    try:
        await order.fetch_link("initiator")
    except Exception as exc:
        if app_config.DEBUG_MODE:
            _logger.warning(
                f"Failed to fetch initiator link for order_id={order.id}",
                extra_tag="Cards",
                exc=exc,
            )


def _get_user_display_name(user: Any) -> str:
    if isinstance(user, (TelegramUser, PassiveUser)):
        return user.display_name
    return "(unknown)"


def _format_history_datetime(value: Any) -> str:
    return format_date(value, fmt="%d.%m %H:%M")


def _format_session_totals_lines(totals: Dict[str, int]) -> List[str]:
    if not totals:
        return []

    # Sort by coffees desc, then name.
    sorted_items = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    return [f"   - {display_name}: {coffees}" for display_name, coffees in sorted_items]


async def _find_orders_for_card(card: CoffeeCard) -> List[CoffeeOrder]:
    orders_by_id: dict[str, CoffeeOrder] = {}

    query_variants: List[dict[str, Any]] = [
        {"coffee_cards.$id": card.id},
        {"coffee_cards": card.id},
        {"coffee_cards._id": card.id},
    ]

    for query in query_variants:
        matched: List[CoffeeOrder] = (
            await CoffeeOrder.find(query, fetch_links=True).sort("-order_date").to_list()
        )  # type: ignore[arg-type]
        for order in matched:
            if order.id is None:
                continue
            orders_by_id[str(order.id)] = order

    # Fallback: if card keeps an explicit order list, merge it too.
    try:
        await card.fetch_link("orders")
    except Exception:
        pass

    for linked in card.orders:
        if isinstance(linked, CoffeeOrder) and linked.id is not None:
            orders_by_id.setdefault(str(linked.id), linked)

    orders = sorted(orders_by_id.values(), key=lambda o: o.order_date, reverse=True)

    return orders


async def _find_orders_for_session(session: CoffeeSession) -> List[CoffeeOrder]:
    if session.id is None:
        return []

    orders_by_id: dict[str, CoffeeOrder] = {}

    query_variants: List[dict[str, Any]] = [
        {"session.$id": session.id},
        {"session": session.id},
        {"session._id": session.id},
    ]

    for query in query_variants:
        matched: List[CoffeeOrder] = (
            await CoffeeOrder.find(query, fetch_links=False).sort("-order_date").to_list()
        )  # type: ignore[arg-type]
        for order in matched:
            if order.id is None:
                continue
            orders_by_id[str(order.id)] = order

    # Fallback: merge explicit session order list.
    try:
        await session.fetch_link("orders")
    except Exception:
        pass

    for linked in session.orders:
        if isinstance(linked, CoffeeOrder) and linked.id is not None:
            orders_by_id.setdefault(str(linked.id), linked)

    return sorted(orders_by_id.values(), key=lambda o: o.order_date, reverse=True)


async def list_history_for_selected_card(flow_state, api, user_id: int) -> List[CardHistoryItem]:
    card_id = flow_state.get(KEY_SELECTED_CARD_ID)
    if not isinstance(card_id, str) or not card_id:
        return []

    card = await CoffeeCard.get(card_id)
    if not card or card.id is None:
        return []

    orders_for_card = await _find_orders_for_card(card)

    # Ensure we have names available for card orders.
    for order in orders_for_card:
        await _fetch_order_people(order)

    # Group by explicit session link on the order.
    sessions_by_id: dict[str, CoffeeSession] = {}
    for order in orders_for_card:
        if order.session is None:
            continue

        if not isinstance(order.session, CoffeeSession):
            try:
                await order.fetch_link("session")
            except Exception:
                continue

        if isinstance(order.session, CoffeeSession) and order.session.id is not None:
            sessions_by_id[str(order.session.id)] = order.session

    sessions = sorted(sessions_by_id.values(), key=lambda s: s.session_date, reverse=True)

    timeline: List[tuple[datetime, str]] = []

    for session in sessions:
        session_orders = await _find_orders_for_session(session)
        if not session_orders:
            # Fallback: at least include the card orders we know belong to this session.
            session_orders = [
                o
                for o in orders_for_card
                if isinstance(o.session, CoffeeSession) and o.session.id == session.id
            ]

        for order in session_orders:
            await _fetch_order_people(order)

        # Totals for the whole session (across all cards).
        totals: Dict[str, int] = {}
        for order in session_orders:
            consumer_name = _get_user_display_name(order.consumer)
            totals[consumer_name] = totals.get(consumer_name, 0) + int(order.quantity)

        # Orders from this session that used the selected card.
        orders_using_this_card = [
            o
            for o in orders_for_card
            if isinstance(o.session, CoffeeSession)
            and o.session.id == session.id
        ]

        if not orders_using_this_card:
            continue

        initiator_name = "(unknown)"
        try:
            await session.fetch_link("initiator")
            initiator_name = session.initiator.display_name  # type: ignore[attr-defined]
        except Exception:
            pass

        header = f"{_format_history_datetime(session.session_date)} - **Session** ({initiator_name})"
        totals_lines = "\n".join(_format_session_totals_lines(totals))
        text = header
        if totals_lines:
            text = f"{text}\n{totals_lines}\n"
        timeline.append((session.session_date, text))

    # Quick orders: orders without a session link.
    quick_orders: List[CoffeeOrder] = [o for o in orders_for_card if o.session is None]

    for order in quick_orders:
        consumer_name = _get_user_display_name(order.consumer)
        initiator_name = _get_user_display_name(order.initiator)

        coffees_from_this_card = str(int(order.quantity))

        timeline.append(
            (
                order.order_date,
                (
                    f"{_format_history_datetime(order.order_date)} - **Quick Order** ({initiator_name})\n"
                    f"   - {consumer_name}: {coffees_from_this_card}\n"
                ),
            )
        )

    if not timeline:
        return [CardHistoryItem(text="(no orders yet)")]

    timeline.sort(key=lambda kv: kv[0], reverse=True)
    return [CardHistoryItem(text=text) for _dt, text in timeline]


def format_history_item(item: CardHistoryItem, index: int) -> str:
    return item.text


async def handle_card_details_button(data: str, flow_state, api, user_id: int) -> Optional[str]:
    if data != CommonCallbacks.CLOSE:
        return None

    back_state = flow_state.get(KEY_DETAILS_BACK_STATE, STATE_MENU)
    if not isinstance(back_state, str) or not back_state:
        return STATE_MENU
    return back_state


def format_card_details(card: CoffeeCard, index: int) -> str:
    purchaser = cast(TelegramUser, card.purchaser)  # fetch_links=True
    status = "🟢 Active" if card.is_active else "⚪ Completed"
    completed_line = ""
    if not card.is_active and card.completed_at:
        completed_line = f"Completed: {format_date(card.completed_at)}\n"

    return (
        f"**{card.name}** — {status}\n"
        f"Remaining: {card.remaining_coffees}/{card.total_coffees}\n"
        f"Created: {format_date(card.created_at)}\n"
        f"{completed_line}"
        f"Purchaser: {purchaser.display_name}\n"
    )


# ============================================================================
# CREATE CARD FLOW (shared)
# ============================================================================


async def build_create_main_text(flow_state, api, user_id: int) -> str:
    user = await api.conversation_manager.repo.find_user_by_id(user_id)
    paypal_link = user.paypal_link if user and user.paypal_link else "(not set)"

    total_coffees = int(flow_state.get(KEY_TOTAL_COFFEES, DEFAULT_TOTAL_COFFEES))
    cost_per_coffee = float(flow_state.get(KEY_COST_PER_COFFEE, DEFAULT_COST_PER_COFFEE))
    total_cost = total_coffees * cost_per_coffee

    return (
        "☕ **Create New Coffee Card**\n\n"
        f"**Total Coffees:** {total_coffees}\n"
        f"**Cost per Coffee:** €{cost_per_coffee:.2f}\n"
        f"**Total Cost:** €{total_cost:.2f}\n\n"
        f"**Your PayPal Link:** {paypal_link}\n\n"
        "Adjust values or create the card."
    )


async def handle_cancel_to_after_cancel(data: str, flow_state, api, user_id: int) -> Optional[str]:
    if data == CommonCallbacks.CANCEL:
        after_cancel = flow_state.get(CommonFlowKeys.AFTER_CANCEL, CommonStateIds.EXIT_CANCELLED)
        if not isinstance(after_cancel, str) or not after_cancel:
            return CommonStateIds.EXIT_CANCELLED
        return after_cancel
    return None


async def build_create_total_text(flow_state, api, user_id: int) -> str:
    validation_error = flow_state.pop("create_total_error", None)
    if validation_error:
        return validation_error

    current = int(flow_state.get(KEY_TOTAL_COFFEES, DEFAULT_TOTAL_COFFEES))
    return (
        "📝 **Total Coffees**\n\n"
        f"Current value: **{current}**\n\n"
        "Type the new total number of coffees (e.g. `200`)."
    )


async def handle_create_total_input(input_text: str, flow_state, api, user_id: int) -> Optional[str]:
    parser = IntegerParser()
    value = parser.parse(input_text)

    if value is None or value <= 0:
        flow_state.set(
            "create_total_error",
            "❌ **Invalid number**\n\n"
            "Please enter a positive whole number (e.g. `200`).",
        )
        return STATE_CREATE_TOTAL

    if value > 2000:
        flow_state.set(
            "create_total_error",
            "❌ **Too large**\n\n"
            "Please enter a value up to **2000**.",
        )
        return STATE_CREATE_TOTAL

    flow_state.set(KEY_TOTAL_COFFEES, value)
    return STATE_CREATE_MAIN


async def build_create_price_text(flow_state, api, user_id: int) -> str:
    validation_error = flow_state.pop("create_price_error", None)
    if validation_error:
        return validation_error

    current = float(flow_state.get(KEY_COST_PER_COFFEE, DEFAULT_COST_PER_COFFEE))
    return (
        "💰 **Cost per Coffee**\n\n"
        f"Current value: **€{current:.2f}**\n\n"
        "Type the new price in EUR (examples: `0.8`, `0,80`, `€0,80`)."
    )


async def handle_create_price_input(input_text: str, flow_state, api, user_id: int) -> Optional[str]:
    parser = MoneyParser(currency_symbol="€")
    value = parser.parse(input_text)

    if value is None or value <= 0:
        flow_state.set(
            "create_price_error",
            "❌ **Invalid price**\n\n"
            "Please enter a positive amount (examples: `0.8`, `0,80`, `€0,80`).",
        )
        return STATE_CREATE_PRICE

    if value > 100:
        flow_state.set(
            "create_price_error",
            "❌ **Too large**\n\n"
            "Please enter a value up to **100 €**.",
        )
        return STATE_CREATE_PRICE

    flow_state.set(KEY_COST_PER_COFFEE, float(value))
    return STATE_CREATE_MAIN


async def build_create_confirm_text(flow_state, api, user_id: int) -> str:
    total_coffees = int(flow_state.get(KEY_TOTAL_COFFEES, DEFAULT_TOTAL_COFFEES))
    cost_per_coffee = float(flow_state.get(KEY_COST_PER_COFFEE, DEFAULT_COST_PER_COFFEE))
    total_cost = total_coffees * cost_per_coffee

    return (
        "✅ **Confirm Creation**\n\n"
        f"**Total Coffees:** {total_coffees}\n"
        f"**Cost per Coffee:** €{cost_per_coffee:.2f}\n"
        f"**Total Cost:** €{total_cost:.2f}\n\n"
        "Create this coffee card now?"
    )


async def execute_create_card(flow_state, api, user_id: int) -> None:
    total_coffees = int(flow_state.get(KEY_TOTAL_COFFEES, DEFAULT_TOTAL_COFFEES))
    cost_per_coffee = float(flow_state.get(KEY_COST_PER_COFFEE, DEFAULT_COST_PER_COFFEE))

    try:
        card = await api.coffee_card_manager.create_coffee_card(
            total_coffees=total_coffees,
            cost_per_coffee=cost_per_coffee,
            purchaser_id=int(user_id),
        )
        flow_state.set("created_card", card)
        flow_state.set("create_error", None)
    except Exception as exc:
        flow_state.set("created_card", None)
        flow_state.set("create_error", f"{type(exc).__name__}: {exc}")


async def build_create_result_text(flow_state, api, user_id: int) -> str:
    error = flow_state.get("create_error")
    if error:
        return (
            "❌ **Failed to create coffee card**\n\n"
            f"{error}"
        )

    card = flow_state.get("created_card")
    if not card:
        return "❌ **Failed to create coffee card**"

    return (
        "✅ **Coffee Card Created**\n\n"
        f"**Name:** {card.name}\n"
        f"**Total Coffees:** {card.total_coffees}\n"
        f"**Cost per Coffee:** €{card.cost_per_coffee:.2f}\n"
        f"**Total Cost:** €{card.total_cost:.2f}\n\n"
        "The card is now active."
    )


create_main_buttons: List[List[ButtonCallback]] = [
    [ButtonCallback("✅ Create Card", STATE_CREATE_CONFIRM)],
    [
        ButtonCallback("📝 Set Total Coffees", STATE_CREATE_TOTAL),
        ButtonCallback("💰 Set Price", STATE_CREATE_PRICE),
    ],
    [ButtonCallback("❌ Cancel", CommonCallbacks.CANCEL)],
]

create_main_defaults: dict[str, Any] = {
    KEY_TOTAL_COFFEES: DEFAULT_TOTAL_COFFEES,
    KEY_COST_PER_COFFEE: DEFAULT_COST_PER_COFFEE,
    CommonFlowKeys.AFTER_CANCEL: CommonStateIds.EXIT_CANCELLED,
}

create_main_allowlist: List[str] = [STATE_CREATE_TOTAL, STATE_CREATE_PRICE, STATE_CREATE_CONFIRM]

create_total_buttons: List[List[ButtonCallback]] = [
    [ButtonCallback("◁ Back", STATE_CREATE_MAIN)],
    [ButtonCallback("❌ Cancel", CommonCallbacks.CANCEL)],
]
create_total_allowlist: List[str] = [STATE_CREATE_MAIN]

create_price_buttons: List[List[ButtonCallback]] = [
    [ButtonCallback("◁ Back", STATE_CREATE_MAIN)],
    [ButtonCallback("❌ Cancel", CommonCallbacks.CANCEL)],
]
create_price_allowlist: List[str] = [STATE_CREATE_MAIN]

create_confirm_buttons: List[List[ButtonCallback]] = [
    [ButtonCallback("◁ Back", STATE_CREATE_MAIN), ButtonCallback("✅ Yes, Create", STATE_CREATE_EXECUTE)],
    [ButtonCallback("❌ Cancel", CommonCallbacks.CANCEL)],
]
create_confirm_allowlist: List[str] = [STATE_CREATE_EXECUTE, STATE_CREATE_MAIN]


# ============================================================================
# CLOSE CARD FLOW (shared)
# ============================================================================


async def build_close_confirm_text(flow_state, api, user_id: int) -> str:
    card = await api.coffee_card_manager.get_oldest_active_coffee_card()
    if not card:
        return "❌ No active coffee cards found."

    purchaser_name = "(unknown)"
    try:
        await card.fetch_link("purchaser")
        purchaser_name = card.purchaser.display_name  # type: ignore[attr-defined]
    except Exception:
        pass

    warning = ""
    if card.remaining_coffees > 0:
        warning = f"\n\n⚠️ This card still has **{card.remaining_coffees}** coffees left."

    return (
        "✅ **Close Coffee Card**\n\n"
        f"**Card:** {card.name}\n"
        f"**Remaining:** {card.remaining_coffees}/{card.total_coffees}\n"
        f"**Created:** {format_date(card.created_at)}\n"
        f"**Purchaser:** {purchaser_name}"
        f"{warning}\n\n"
        "Close it now?"
    )


async def execute_close_card(flow_state, api, user_id: int) -> None:
    card = await api.coffee_card_manager.get_oldest_active_coffee_card()
    if not card:
        flow_state.set("close_error", "No active coffee cards found")
        return

    try:
        flow_state.set("closed_card_name", card.name)
        await api.coffee_card_manager.close_card(card, requesting_user_id=int(user_id))
        flow_state.set("close_error", None)
    except Exception as exc:
        flow_state.set("close_error", f"{type(exc).__name__}: {exc}")


async def build_close_result_text(flow_state, api, user_id: int) -> str:
    error = flow_state.get("close_error")
    if error:
        return f"❌ **Failed to close card**\n\n{error}"

    name = flow_state.get("closed_card_name") or "(unknown)"

    return (
        "✅ **Coffee Card Closed**\n\n"
        f"Card **{name}** was closed successfully."
    )


close_confirm_buttons: List[List[ButtonCallback]] = [
    [ButtonCallback("✅ Yes, Close", STATE_CLOSE_EXECUTE)],
    [ButtonCallback("❌ Cancel", CommonCallbacks.CANCEL)],
]

close_confirm_defaults: dict[str, Any] = {
    CommonFlowKeys.AFTER_CANCEL: CommonStateIds.EXIT_CANCELLED,
}

close_confirm_allowlist: List[str] = [STATE_CLOSE_EXECUTE]


# ============================================================================
# FLOW FACTORIES
# ============================================================================


def _create_common_flow() -> MessageFlow:
    flow = MessageFlow()

    flow.add_state(
        MessageDefinition(
            state_id=STATE_CREATE_MAIN,
            state_type=StateType.BUTTON,
            text_builder=build_create_main_text,
            buttons=create_main_buttons,
            timeout=120,
            exit_buttons=[],
            defaults=create_main_defaults,
            on_button_press=handle_cancel_to_after_cancel,
            route_callback_to_state_id=True,
            route_callback_allowlist=create_main_allowlist,
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_CREATE_TOTAL,
            state_type=StateType.MIXED,
            text_builder=build_create_total_text,
            buttons=create_total_buttons,
            input_validator=TextLengthValidator(min_length=1, max_length=20),
            input_timeout=120,
            exit_buttons=[],
            on_input_received=handle_create_total_input,
            on_button_press=handle_cancel_to_after_cancel,
            route_callback_to_state_id=True,
            route_callback_allowlist=create_total_allowlist,
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_CREATE_PRICE,
            state_type=StateType.MIXED,
            text_builder=build_create_price_text,
            buttons=create_price_buttons,
            input_validator=TextLengthValidator(min_length=1, max_length=20),
            input_timeout=120,
            exit_buttons=[],
            on_input_received=handle_create_price_input,
            on_button_press=handle_cancel_to_after_cancel,
            route_callback_to_state_id=True,
            route_callback_allowlist=create_price_allowlist,
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_CREATE_CONFIRM,
            state_type=StateType.BUTTON,
            text_builder=build_create_confirm_text,
            buttons=create_confirm_buttons,
            timeout=60,
            exit_buttons=[],
            on_button_press=handle_cancel_to_after_cancel,
            route_callback_to_state_id=True,
            route_callback_allowlist=create_confirm_allowlist,
        )
    )

    return flow


def _create_execute_state(*, auto_exit: bool, back_state: str | None) -> MessageDefinition:
    if auto_exit:
        return MessageDefinition(
            state_id=STATE_CREATE_EXECUTE,
            state_type=StateType.BUTTON,
            text_builder=build_create_result_text,
            buttons=None,
            auto_exit_after_render=True,
            on_enter=execute_create_card,
        )

    if back_state is None:
        raise ValueError("back_state is required when auto_exit=False")

    return MessageDefinition(
        state_id=STATE_CREATE_EXECUTE,
        state_type=StateType.BUTTON,
        text_builder=build_create_result_text,
        buttons=[[ButtonCallback("◁ Back", back_state)]],
        timeout=120,
        exit_buttons=[],
        on_enter=execute_create_card,
        route_callback_to_state_id=True,
        route_callback_allowlist=[back_state],
    )


def _close_common_flow() -> MessageFlow:
    flow = MessageFlow()

    flow.add_state(
        MessageDefinition(
            state_id=STATE_CLOSE_CONFIRM,
            state_type=StateType.BUTTON,
            text_builder=build_close_confirm_text,
            buttons=close_confirm_buttons,
            timeout=60,
            exit_buttons=[],
            defaults=close_confirm_defaults,
            on_button_press=handle_cancel_to_after_cancel,
            route_callback_to_state_id=True,
            route_callback_allowlist=close_confirm_allowlist,
        )
    )

    return flow


def _close_execute_state(*, auto_exit: bool, back_state: str | None) -> MessageDefinition:
    if auto_exit:
        return MessageDefinition(
            state_id=STATE_CLOSE_EXECUTE,
            state_type=StateType.BUTTON,
            text_builder=build_close_result_text,
            buttons=None,
            auto_exit_after_render=True,
            on_enter=execute_close_card,
        )

    if back_state is None:
        raise ValueError("back_state is required when auto_exit=False")

    return MessageDefinition(
        state_id=STATE_CLOSE_EXECUTE,
        state_type=StateType.BUTTON,
        text_builder=build_close_result_text,
        buttons=[[ButtonCallback("◁ Back", back_state)]],
        timeout=120,
        exit_buttons=[],
        on_enter=execute_close_card,
        route_callback_to_state_id=True,
        route_callback_allowlist=[back_state],
    )


def create_card_menu_flow() -> MessageFlow:
    """`/cards` main menu flow."""
    flow = MessageFlow()

    flow.extend(_create_common_flow())
    flow.extend(_close_common_flow())

    flow.add_state(
        MessageDefinition(
            state_id=STATE_MENU,
            state_type=StateType.BUTTON,
            text_builder=build_card_menu_text,
            buttons=menu_buttons,
            defaults=menu_defaults,
            on_button_press=handle_card_menu_button,
            route_callback_to_state_id=True,
            route_callback_allowlist=menu_allowlist,
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_CARDS_LIST,
            state_type=StateType.BUTTON,
            text="📋 **All Coffee Cards**",
            pagination_config=PaginationConfig(page_size=5, items_per_row=5, close_button_text="◁ Back"),
            pagination_items_builder=list_all_cards,
            pagination_item_formatter=format_card_details,
            pagination_item_button_builder=build_card_number_button,
            pagination_reset_on_enter=False,
            exit_buttons=[],
            on_button_press=handle_cards_list_button,
            next_state_map={CommonCallbacks.CLOSE: STATE_MENU},
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_CARD_DETAILS,
            state_type=StateType.BUTTON,
            text_builder=build_card_details_text,
            pagination_config=PaginationConfig(page_size=3, items_per_row=1, close_button_text="◁ Back"),
            pagination_items_builder=list_history_for_selected_card,
            pagination_item_formatter=format_history_item,
            pagination_reset_on_enter=True,
            exit_buttons=[],
            on_button_press=handle_card_details_button,
        )
    )

    flow.add_state(_create_execute_state(auto_exit=True, back_state=None))
    flow.add_state(_close_execute_state(auto_exit=True, back_state=None))

    return flow


def create_new_card_flow() -> MessageFlow:
    """`/new_card` flow."""
    flow = MessageFlow()

    flow.extend(_create_common_flow())
    flow.add_state(_create_execute_state(auto_exit=True, back_state=None))

    flow.add_state(
        ExitStateBuilder.create_cancelled(
            state_id=CommonStateIds.EXIT_CANCELLED,
            message="❌ **Coffee Card Creation Cancelled**\n\nNo changes were made.",
        )
    )

    return flow


def create_close_card_flow() -> MessageFlow:
    """`/close_card` flow."""
    flow = MessageFlow()

    flow.extend(_close_common_flow())
    flow.add_state(_close_execute_state(auto_exit=True, back_state=None))

    flow.add_state(
        ExitStateBuilder.create_cancelled(
            state_id=CommonStateIds.EXIT_CANCELLED,
            message="❌ **Close Card Cancelled**\n\nNo changes were made.",
        )
    )

    return flow
