"""Quick ordering flow (MessageFlow-based).

Triggered by sending a plain number to the bot (see `handle_digits_command`).

Design goals:
- Keep the confirm UI in a single editable message
- Perform the actual order inside the confirm button handler so the user sees
  the order summary before any card completion notifications are sent.
"""

from __future__ import annotations

from typing import Any, Optional

from ..message_flow import ButtonCallback, MessageAction, MessageDefinition, MessageFlow
from ..message_flow_helpers import CommonCallbacks, make_state
from ...common.log import Logger
from ...exceptions.coffee_exceptions import InsufficientCoffeeError
from ...models.coffee_models import CoffeeCard
from ...services.gsheet_sync import request_gsheet_sync_after_action
from ...services.order import place_order


_logger = Logger("QuickOrderFlow")


STATE_CONFIRM = "quick_order_confirm"

STATE_EXIT_SUCCESS = "quick_order_exit_success"
STATE_EXIT_CANCELLED = "quick_order_exit_cancelled"
STATE_EXIT_NOT_ENOUGH = "quick_order_exit_not_enough"
STATE_EXIT_FAILED = "quick_order_exit_failed"

KEY_QUANTITY = "quantity"
KEY_INITIATOR = "initiator"
KEY_AVAILABLE = "available"


def format_not_enough_coffees_text(quantity: int, available: int) -> str:
    return (
        "❌ Not enough coffees available right now.\n\n"
        f"Requested: **{quantity}**\n"
        f"Available: **{available}**"
    )


async def _build_confirm_text(flow_state, api: Any, user_id: int) -> str:
    quantity = int(flow_state.get(KEY_QUANTITY))
    initiator = flow_state.get(KEY_INITIATOR)

    available = await api.coffee_card_manager.get_available()

    split_preview_lines: list[str] = []
    try:
        preview_cards = await api.coffee_card_manager.get_coffee_cards_for_order(quantity)
        if len(preview_cards) > 1:
            remaining = int(quantity)
            for card in preview_cards:
                take = min(remaining, max(0, int(card.remaining_coffees)))
                if take <= 0:
                    continue
                split_preview_lines.append(f"• **{card.name}**: **{take}**")
                remaining -= take
                if remaining <= 0:
                    break
    except Exception:
        split_preview_lines = []

    split_preview_text = ""
    if split_preview_lines:
        split_preview_text = "\n\n⚠️ This order will be split across multiple cards:\n" + "\n".join(
            split_preview_lines
        )

    return (
        "☕ **Quick Order**\n\n"
        f"Order **{quantity}** coffee(s) for **{initiator.display_name}**?\n"
        f"Available right now: **{available}**"
        f"{split_preview_text}\n\n"
        "Confirm?"
    )


async def _handle_confirm_button(data: str, flow_state, api: Any, user_id: int) -> Optional[str]:
    if data == CommonCallbacks.CANCEL:
        return STATE_EXIT_CANCELLED

    if data != CommonCallbacks.CONFIRM:
        return None

    quantity = int(flow_state.get(KEY_QUANTITY))
    initiator = flow_state.get(KEY_INITIATOR)

    cached_available = await api.coffee_card_manager.get_available()
    if quantity > cached_available:
        await api.coffee_card_manager.load_from_db()

    available = await api.coffee_card_manager.get_available()
    if quantity > available:
        flow_state.set(KEY_AVAILABLE, int(available))
        return STATE_EXIT_NOT_ENOUGH

    await api.conversation_manager.send_or_edit_message(
        user_id,
        "⏳ Placing order...",
        flow_state.current_message,
        remove_buttons=True,
        delete_after=5,
    )

    try:
        snapshot_manager = api.get_snapshot_manager()
        async with snapshot_manager.pending_snapshot(
            reason=f"Quick Order ({initiator.display_name} x{quantity})",
            context="quick_order",
            collections=("coffee_cards", "coffee_orders", "user_debts", "payments"),
        ):
            cards = await api.coffee_card_manager.get_coffee_cards_for_order(quantity)
            _logger.debug(
                f"Quick order using {len(cards)} card(s): {', '.join([c.name for c in cards])}"
            )

            orders = await place_order(
                initiator=initiator,
                consumer=initiator,
                cards=cards,
                quantity=quantity,
                session=None,
                enforce_capacity=True,
            )

            split_summary_lines: list[str] = []
            if len(orders) > 1:
                for order in orders:
                    card_name = "(unknown)"
                    try:
                        linked_card = order.coffee_cards[0]
                        if isinstance(linked_card, CoffeeCard):
                            card_name = linked_card.name
                    except Exception:
                        card_name = "(unknown)"

                    split_summary_lines.append(f"• **{card_name}**: **{int(order.quantity)}**")

            split_summary_text = ""
            if split_summary_lines:
                split_summary_text = "\n\n⚠️ Split across cards:\n" + "\n".join(split_summary_lines)

            # Send the order summary BEFORE any auto-close card completion notifications.
            await api.message_manager.send_text(
                user_id,
                f"✅ Ordered **{quantity}** coffee(s) for **{initiator.display_name}**.{split_summary_text}",
                True,
                True,
            )

            cards_to_close = [
                card for card in cards if bool(card.is_active) and int(card.remaining_coffees) == 0
            ]
            if cards_to_close:
                _logger.debug(
                    f"Quick order closing filled card(s): {', '.join([c.name for c in cards_to_close])}"
                )

            for card in cards_to_close:
                await api.coffee_card_manager.close_card(
                    card,
                    requesting_user_id=user_id,
                    closed_by_session=False,
                )

            # Refresh cached counts after mutating cards
            await api.coffee_card_manager.load_from_db()

            request_gsheet_sync_after_action(reason="quick_order_completed")

        return STATE_EXIT_SUCCESS

    except InsufficientCoffeeError as e:
        flow_state.set(KEY_AVAILABLE, int(e.available))
        return STATE_EXIT_NOT_ENOUGH
    except Exception as e:
        _logger.error("Quick order failed", exc=e)
        return STATE_EXIT_FAILED


def create_quick_order_flow() -> MessageFlow:
    flow = MessageFlow()

    flow.add_state(
        make_state(
            STATE_CONFIRM,
            text_builder=_build_confirm_text,
            buttons=[
                [ButtonCallback("✅ Confirm", CommonCallbacks.CONFIRM)],
                [ButtonCallback("❌ Cancel", CommonCallbacks.CANCEL)],
            ],
            action=MessageAction.EDIT,
            timeout=30,
            exit_buttons=[],
            on_button_press=_handle_confirm_button,
        )
    )

    async def _build_not_enough_text(flow_state, api, user_id: int) -> str:
        quantity = int(flow_state.get(KEY_QUANTITY))
        available = int(flow_state.get(KEY_AVAILABLE, 0))
        return format_not_enough_coffees_text(quantity, available)

    flow.add_state(
        MessageDefinition(
            state_id=STATE_EXIT_NOT_ENOUGH,
            text_builder=_build_not_enough_text,
            buttons=None,
            action=MessageAction.EDIT,
            timeout=1,
            remove_buttons_on_exit=True,
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_EXIT_CANCELLED,
            text="❌ Order cancelled.",
            buttons=None,
            action=MessageAction.EDIT,
            timeout=1,
            remove_buttons_on_exit=True,
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_EXIT_FAILED,
            text="❌ Quick order failed due to an unexpected error.",
            buttons=None,
            action=MessageAction.EDIT,
            timeout=1,
            remove_buttons_on_exit=True,
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id=STATE_EXIT_SUCCESS,
            text="",
            buttons=None,
            action=MessageAction.EDIT,
            timeout=1,
            remove_buttons_on_exit=True,
        )
    )

    return flow
