from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot.conversation_flows.debt_flow import (
    build_main_keyboard,
    create_debt_flow,
    handle_creditor_debts_button,
    handle_creditor_debts_input,
)
from src.bot.message_flow import MessageFlowState


class FakeDebt:
    def __init__(self, debt_id: str, creditor_name: str, card_name: str, total_amount: float, paid_amount: float):
        self.id = debt_id
        self.creditor = SimpleNamespace(display_name=creditor_name, paypal_link=None)
        self.coffee_card = SimpleNamespace(name=card_name)
        self.total_amount = total_amount
        self.paid_amount = paid_amount
        self.debt_correction = 0.0
        self.is_settled = False
        self.created_at = datetime.now()


def make_api(debts):
    api = SimpleNamespace()
    api.conversation_manager = SimpleNamespace(
        repo=SimpleNamespace(find_user_by_id=AsyncMock(return_value=SimpleNamespace(user_id=1)))
    )
    api.debt_manager = SimpleNamespace(
        get_user_debts=AsyncMock(return_value=debts),
        _apply_payment_to_debt=AsyncMock(),
    )
    api.message_manager = SimpleNamespace(send_text=AsyncMock())
    api.get_snapshot_manager = lambda: SimpleNamespace(create_snapshot=AsyncMock())
    return api


@pytest.mark.asyncio
async def test_build_main_keyboard_contains_creditor_buttons_and_close():
    debts = [
        FakeDebt("d1", "Charlie", "Card A", total_amount=3.0, paid_amount=0.0),
        FakeDebt("d2", "Alice", "Card B", total_amount=5.0, paid_amount=0.0),
    ]
    api = make_api(debts)
    flow_state = MessageFlowState(current_state_id="main")

    keyboard = await build_main_keyboard(flow_state, api, 123)

    callback_rows = [[button.callback_data for button in row] for row in keyboard]
    assert callback_rows[0] == ["creditor:Alice", "creditor:Charlie"]
    assert callback_rows[-1] == ["close"]


@pytest.mark.asyncio
async def test_custom_amount_input_stages_distribution_without_applying():
    debts = [
        FakeDebt("d1", "Alice", "Card 1", total_amount=5.0, paid_amount=0.0),
        FakeDebt("d2", "Alice", "Card 2", total_amount=4.0, paid_amount=1.0),
    ]
    api = make_api(debts)
    flow_state = MessageFlowState(
        current_state_id="creditor_debts",
        flow_data={
            "selected_creditor": "Alice",
            "creditor_debts": {
                "d1": {"card_name": "Card 1", "amount": 5.0, "debt": debts[0]},
                "d2": {"card_name": "Card 2", "amount": 3.0, "debt": debts[1]},
            },
        },
    )

    next_state = await handle_creditor_debts_input("6", flow_state, api, 123)

    assert next_state == "creditor_debts"
    staged = flow_state.get("staged_payments", {})
    assert staged["d1"] == 5.0
    assert staged["d2"] == 1.0
    api.debt_manager._apply_payment_to_debt.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_applies_staged_payments_and_returns_main():
    debts = [
        FakeDebt("d1", "Alice", "Card 1", total_amount=5.0, paid_amount=0.0),
        FakeDebt("d2", "Alice", "Card 2", total_amount=4.0, paid_amount=1.0),
    ]
    api = make_api(debts)
    flow_state = MessageFlowState(
        current_state_id="creditor_debts",
        flow_data={
            "selected_creditor": "Alice",
            "creditor_debts": {
                "d1": {"card_name": "Card 1", "amount": 5.0, "debt": debts[0]},
                "d2": {"card_name": "Card 2", "amount": 3.0, "debt": debts[1]},
            },
            "staged_payments": {"d1": 5.0, "d2": 1.0},
        },
    )

    next_state = await handle_creditor_debts_button("save", flow_state, api, 123)

    assert next_state == "main"
    assert api.debt_manager._apply_payment_to_debt.await_count == 2
    api.message_manager.send_text.assert_awaited_once()


def test_create_debt_flow_contains_expected_states():
    flow = create_debt_flow()
    assert set(flow.states.keys()) == {"main", "creditor_debts"}
