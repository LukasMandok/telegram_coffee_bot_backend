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
    RegexValidator,
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
    build_text_input_handler,
    format_date,
    format_money,
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
STATE_CARD_DEBTS = "card_debts"

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

KEY_SELECTED_CARD_VIEW_DATA_CARD_ID = "selected_card_view_data_card_id"
KEY_SELECTED_CARD_DEBTS_OVERVIEW_ITEMS = "selected_card_debts_overview_items"
KEY_SELECTED_CARD_DEBTS_OVERVIEW_TOTAL = "selected_card_debts_overview_total"
KEY_SELECTED_CARD_DEBTS_OVERVIEW_PAID = "selected_card_debts_overview_paid"


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
            _reset_selected_card_view_state(flow_state)
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
    _reset_selected_card_view_state(flow_state)
    return STATE_CARD_DETAILS


async def resolve_card_number_matches(
    typed_number: int,
    flow_state,
    api,
    user_id: int,
) -> List[str]:
    cards = await list_all_cards(flow_state, api, user_id)

    matches: List[str] = []
    for idx, card in enumerate(cards):
        match = re.search(r"(\d+)(?!.*\d)", card.name)
        label = match.group(1) if match else str(idx + 1)
        try:
            label_number = int(label)
        except ValueError:
            continue

        if label_number == typed_number and card.id is not None:
            matches.append(str(card.id))

    return matches


async def _select_card_from_match(card_id: str, flow_state, api, user_id: int) -> Optional[str]:
    flow_state.set(KEY_SELECTED_CARD_ID, card_id)
    flow_state.set(KEY_DETAILS_BACK_STATE, STATE_CARDS_LIST)
    _reset_selected_card_view_state(flow_state)
    return STATE_CARD_DETAILS


def _reset_selected_card_view_state(flow_state) -> None:
    """Clear pagination + derived data when the selected card changes."""
    flow_state.pagination_state.pop(STATE_CARD_DETAILS, None)
    flow_state.pagination_state.pop(STATE_CARD_DEBTS, None)
    flow_state.clear(
        KEY_SELECTED_CARD_VIEW_DATA_CARD_ID,
        KEY_SELECTED_CARD_DEBTS_OVERVIEW_ITEMS,
        KEY_SELECTED_CARD_DEBTS_OVERVIEW_TOTAL,
        KEY_SELECTED_CARD_DEBTS_OVERVIEW_PAID,
    )


def _get_card_status_parts(card: CoffeeCard) -> tuple[str, str]:
    if card.is_active:
        return "🟢", "Active"
    return "⚪", "Completed"


def _build_card_orders_debts_header(*, title: str, card: CoffeeCard, purchaser_name: str) -> str:
    indicator, status_name = _get_card_status_parts(card)
    return (
        f"{title}\n\n"
        f"{card.name} - {indicator} {status_name}\n"
        f"Purchaser: {purchaser_name}"
    )


handle_cards_list_input = build_text_input_handler(
    retry_state_id=STATE_CARDS_LIST,
    resolve_matches=resolve_card_number_matches,
    on_match_selected=_select_card_from_match,
    invalid_number_message="❌ Please enter a positive card number (e.g. `17`).",
    not_found_message="❌ Card number **{number}** not found.",
    ambiguous_message="❌ Card number **{number}** is ambiguous. Please use the buttons.",
)


async def build_card_details_text(flow_state, api, user_id: int) -> str:
    card_id = flow_state.get(KEY_SELECTED_CARD_ID)
    if not isinstance(card_id, str) or not card_id:
        return "❌ **Card Orders**\n\nNo card selected."

    card = await CoffeeCard.get(card_id)
    if not card:
        return "❌ **Card Orders**\n\nCard not found."

    purchaser_name = "(unknown)"
    try:
        await card.fetch_link("purchaser")
        purchaser: TelegramUser = card.purchaser  # type: ignore[assignment]
        purchaser_name = purchaser.display_name
    except Exception:
        pass

    completed_value = "-"
    if card.completed_at is not None:
        completed_value = format_date(card.completed_at)

    header = _build_card_orders_debts_header(
        title="Card Orders",
        card=card,
        purchaser_name=purchaser_name,
    )

    return (
        header
        + "\n\n**Details**\n"
        + f"Created: {format_date(card.created_at)}\n"
        + f"Completed: {completed_value}\n"
        + f"Cost: {format_money(card.total_cost)} (per coffee: {format_money(card.cost_per_coffee)})\n\n"
        + "**Order History**"
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


async def list_history_for_selected_card(flow_state, api, user_id: int) -> List[CardHistoryItem]:
    card_id = flow_state.get(KEY_SELECTED_CARD_ID)
    if not isinstance(card_id, str) or not card_id:
        return []

    card = await CoffeeCard.get(card_id)
    if not card or card.id is None:
        return []

    orders_for_card = await api.coffee_card_manager.find_orders_for_card(card)

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
        session_orders = await api.coffee_card_manager.find_orders_for_session(session)
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


async def build_card_debts_text(flow_state, api, user_id: int) -> str:
    card_id = flow_state.get(KEY_SELECTED_CARD_ID)
    if not isinstance(card_id, str) or not card_id:
        return "❌ **Card Debts**\n\nNo card selected."

    card = await CoffeeCard.get(card_id)
    if not card:
        return "❌ **Card Debts**\n\nCard not found."

    purchaser_name = "(unknown)"
    try:
        await card.fetch_link("purchaser")
        purchaser: TelegramUser = card.purchaser  # type: ignore[assignment]
        purchaser_name = purchaser.display_name
    except Exception:
        pass

    await _load_card_debts_overview(flow_state, api, card)

    items: List[CardDebtItem] = flow_state.get(KEY_SELECTED_CARD_DEBTS_OVERVIEW_ITEMS, [])
    total_amount = float(flow_state.get(KEY_SELECTED_CARD_DEBTS_OVERVIEW_TOTAL, 0.0))
    total_paid = float(flow_state.get(KEY_SELECTED_CARD_DEBTS_OVERVIEW_PAID, 0.0))
    total_outstanding = max(0.0, total_amount - total_paid)

    header = _build_card_orders_debts_header(
        title="Card Debts",
        card=card,
        purchaser_name=purchaser_name,
    )

    if card.is_active and not items:
        return header + "\n\nNo debts yet. Debts are created when a card is closed."

    if not items:
        return header + "\n\nNo debts found for this card."

    return (
        header
        + "\n\n**Total Debts**\n"
        + f"Total: **{format_money(total_amount)}**\n"
        + f"Paid: **{format_money(total_paid)}**\n"
        + f"Outstanding: **{format_money(total_outstanding)}**\n\n"
        + "**Debts (per user)**"
    )


@dataclass(frozen=True)
class CardDebtItem:
    debtor_name: str
    total: float
    paid: float


async def _load_card_debts_overview(flow_state, api, card: CoffeeCard) -> None:
    card_id = str(card.id) if card.id is not None else ""
    cached_for = flow_state.get(KEY_SELECTED_CARD_VIEW_DATA_CARD_ID)
    if cached_for == card_id and flow_state.has(KEY_SELECTED_CARD_DEBTS_OVERVIEW_ITEMS):
        return

    debts = await api.coffee_card_manager.find_debts_for_card(card)

    by_debtor: Dict[str, Dict[str, Any]] = {}
    for debt in debts:
        debtor = debt.debtor
        debtor_name = "(unknown)"
        debtor_key = str(debt.id) if debt.id is not None else f"mem:{id(debt)}"
        if isinstance(debtor, (TelegramUser, PassiveUser)):
            debtor_name = debtor.display_name
            debtor_key = debtor.stable_id

        entry = by_debtor.get(debtor_key)
        if entry is None:
            entry = {"name": debtor_name, "total": 0.0, "paid": 0.0}
            by_debtor[debtor_key] = entry

        entry["total"] += float(debt.total_amount)
        entry["paid"] += float(debt.paid_amount)

    items = [
        CardDebtItem(debtor_name=str(v["name"]) or "(unknown)", total=float(v["total"]), paid=float(v["paid"]))
        for v in by_debtor.values()
    ]
    items.sort(key=lambda i: (-(i.total - i.paid), i.debtor_name.lower()))

    total_amount = sum(i.total for i in items)
    total_paid = sum(i.paid for i in items)

    flow_state.set(KEY_SELECTED_CARD_VIEW_DATA_CARD_ID, card_id)
    flow_state.set(KEY_SELECTED_CARD_DEBTS_OVERVIEW_ITEMS, items)
    flow_state.set(KEY_SELECTED_CARD_DEBTS_OVERVIEW_TOTAL, total_amount)
    flow_state.set(KEY_SELECTED_CARD_DEBTS_OVERVIEW_PAID, total_paid)


async def list_debts_for_selected_card(flow_state, api, user_id: int) -> List[CardDebtItem]:
    card_id = flow_state.get(KEY_SELECTED_CARD_ID)
    if not isinstance(card_id, str) or not card_id:
        return []

    card = await CoffeeCard.get(card_id)
    if not card:
        return []

    await _load_card_debts_overview(flow_state, api, card)
    return flow_state.get(KEY_SELECTED_CARD_DEBTS_OVERVIEW_ITEMS, [])


def format_card_debt_item(item: CardDebtItem, index: int) -> str:
    outstanding = max(0.0, item.total - item.paid)
    name = item.debtor_name or "(unknown)"
    paid_text = format_money(item.paid)
    total_text = format_money(item.total)
    outstanding_text = format_money(outstanding)
    return f"- **{name}**:\n`  `paid: {paid_text} / {total_text}`   `remaining: {outstanding_text}"


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
        f"**Cost per Coffee:** {format_money(cost_per_coffee)}\n"
        f"**Total Cost:** {format_money(total_cost)}\n\n"
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
        f"Current value: **{format_money(current)}**\n\n"
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
            f"Please enter a value up to **{format_money(100)}**.",
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
        f"**Cost per Coffee:** {format_money(cost_per_coffee)}\n"
        f"**Total Cost:** {format_money(total_cost)}\n\n"
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
        f"**Cost per Coffee:** {format_money(card.cost_per_coffee)}\n"
        f"**Total Cost:** {format_money(card.total_cost)}\n\n"
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
            state_type=StateType.MIXED,
            text=(
                "📋 **All Coffee Cards**\n\n"
                "Select a card using the buttons, or type its number."
            ),
            pagination_config=PaginationConfig(page_size=5, items_per_row=5, close_button_text="◁ Back"),
            pagination_items_builder=list_all_cards,
            pagination_item_formatter=format_card_details,
            pagination_item_button_builder=build_card_number_button,
            pagination_reset_on_enter=False,
            exit_buttons=[],
            on_button_press=handle_cards_list_button,
            input_validator=RegexValidator(r"^\s*\d+\s*$", error_message="❌ Please type a card number (e.g. `17`)."),
            on_input_received=handle_cards_list_input,
            next_state_map={CommonCallbacks.CLOSE: STATE_MENU},
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_CARD_DETAILS,
            state_type=StateType.BUTTON,
            text_builder=build_card_details_text,
            buttons=[[ButtonCallback("💳 Debts", STATE_CARD_DEBTS)]],
            pagination_config=PaginationConfig(page_size=3, items_per_row=1, close_button_text="◁ Back"),
            pagination_items_builder=list_history_for_selected_card,
            pagination_item_formatter=format_history_item,
            pagination_reset_on_enter=False,
            exit_buttons=[],
            on_button_press=handle_card_details_button,
            route_callback_to_state_id=True,
            route_callback_allowlist=[STATE_CARD_DEBTS],
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_CARD_DEBTS,
            state_type=StateType.BUTTON,
            text_builder=build_card_debts_text,
            buttons=[[ButtonCallback("📜 Orders", STATE_CARD_DETAILS)]],
            pagination_config=PaginationConfig(page_size=10, items_per_row=1, close_button_text="◁ Back"),
            pagination_items_builder=list_debts_for_selected_card,
            pagination_item_formatter=format_card_debt_item,
            pagination_reset_on_enter=False,
            exit_buttons=[],
            on_button_press=handle_card_details_button,
            route_callback_to_state_id=True,
            route_callback_allowlist=[STATE_CARD_DETAILS],
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
