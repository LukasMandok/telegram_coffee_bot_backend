from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot.conversation_flows.debt_flow import (
    build_main_keyboard,
    create_debt_flow,
    handle_confirm_custom_amount_button,
    handle_creditor_payment_button,
    handle_custom_amount_input,
)
from src.bot.message_flow import MessageFlowState


class FakeDebt:
    def __init__(self, total_amount: float, paid_amount: float, debt_correction: float = 0.0):
        self.total_amount = total_amount
        self.paid_amount = paid_amount
        self.debt_correction = debt_correction
        self.save = AsyncMock()


def make_api(summary):
    api = SimpleNamespace()
    api.conversation_manager = SimpleNamespace(
        repo=SimpleNamespace(find_user_by_id=AsyncMock(return_value=SimpleNamespace(user_id=1)))
    )
    debts = []
    for creditor_name, creditor_info in summary.items():
        creditor = SimpleNamespace(
            user_id=creditor_info.get("creditor_id", None),
            display_name=creditor_name,
            paypal_link=creditor_info.get("paypal_link", None),
        )
        for debt in creditor_info.get("debts", []):
            debt.creditor = creditor
            debts.append(debt)

    api.debt_manager = SimpleNamespace(get_user_debts=AsyncMock(return_value=debts))
    api.message_manager = SimpleNamespace(send_text=AsyncMock())
    return api


@pytest.mark.asyncio
async def test_build_main_keyboard_contains_creditor_buttons_and_close():
    charlie_debt = FakeDebt(total_amount=3.0, paid_amount=0.0)
    alice_debt = FakeDebt(total_amount=5.0, paid_amount=0.0)
    summary = {
        "Charlie": {"total_owed": 3.0, "paypal_link": None, "debts": [charlie_debt]},
        "Alice": {"total_owed": 5.0, "paypal_link": None, "debts": [alice_debt]},
    }
    api = make_api(summary)
    flow_state = MessageFlowState(current_state_id="main")

    keyboard = await build_main_keyboard(flow_state, api, 123)

    callback_rows = [[button.callback_data for button in row] for row in keyboard]
    assert callback_rows[0] == ["debt_pay:Alice", "debt_pay:Charlie"]
    assert callback_rows[-1] == ["close"]


@pytest.mark.asyncio
async def test_mark_full_amount_sets_all_debts_to_paid():
    debts = [FakeDebt(total_amount=5.0, paid_amount=1.0), FakeDebt(total_amount=2.0, paid_amount=0.0)]
    summary = {"Alice": {"total_owed": 6.0, "paypal_link": None, "debts": debts}}
    api = make_api(summary)
    flow_state = MessageFlowState(
        current_state_id="creditor_payment",
        flow_data={"selected_creditor_name": "Alice", "debt_summary": summary},
    )

    next_state = await handle_creditor_payment_button("mark_full", flow_state, api, 123)

    assert next_state == "main"
    assert debts[0].paid_amount == 5.0
    assert debts[1].paid_amount == 2.0
    debts[0].save.assert_awaited_once()
    debts[1].save.assert_awaited_once()
    api.message_manager.send_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_custom_amount_input_rejects_too_large_amount():
    debts = [FakeDebt(total_amount=5.0, paid_amount=0.0)]
    summary = {"Alice": {"total_owed": 5.0, "paypal_link": None, "debts": debts}}
    api = make_api(summary)
    flow_state = MessageFlowState(
        current_state_id="custom_amount",
        flow_data={"selected_creditor_name": "Alice", "debt_summary": summary},
    )

    next_state = await handle_custom_amount_input("7", flow_state, api, 123)

    assert next_state == "custom_amount"
    assert "cannot exceed total owed" in flow_state.get("custom_amount_error").lower()
    debts[0].save.assert_not_awaited()


@pytest.mark.asyncio
async def test_custom_amount_input_distributes_payment_sequentially():
    debts = [FakeDebt(total_amount=5.0, paid_amount=0.0), FakeDebt(total_amount=4.0, paid_amount=1.0)]
    summary = {"Alice": {"total_owed": 8.0, "paypal_link": None, "debts": debts}}
    api = make_api(summary)
    flow_state = MessageFlowState(
        current_state_id="custom_amount",
        flow_data={"selected_creditor_name": "Alice", "debt_summary": summary},
    )

    next_state = await handle_custom_amount_input("6", flow_state, api, 123)

    assert next_state == "confirm_custom_amount"
    assert flow_state.get("pending_custom_paid_amount") == 6.0
    assert debts[0].paid_amount == 0.0
    assert debts[1].paid_amount == 1.0
    debts[0].save.assert_not_awaited()
    debts[1].save.assert_not_awaited()
    api.message_manager.send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_custom_amount_applies_payment_and_returns_main():
    debts = [FakeDebt(total_amount=5.0, paid_amount=0.0), FakeDebt(total_amount=4.0, paid_amount=1.0)]
    summary = {"Alice": {"total_owed": 8.0, "paypal_link": None, "debts": debts}}
    api = make_api(summary)
    flow_state = MessageFlowState(
        current_state_id="confirm_custom_amount",
        flow_data={
            "selected_creditor_name": "Alice",
            "debt_summary": summary,
            "pending_custom_paid_amount": 6.0,
        },
    )

    next_state = await handle_confirm_custom_amount_button("confirm_custom", flow_state, api, 123)

    assert next_state == "main"
    assert debts[0].paid_amount == 5.0
    assert debts[1].paid_amount == 2.0
    debts[0].save.assert_awaited_once()
    debts[1].save.assert_awaited_once()
    api.message_manager.send_text.assert_awaited_once()


def test_create_debt_flow_contains_expected_states():
    flow = create_debt_flow()
    assert set(flow.states.keys()) == {"main", "creditor_payment", "custom_amount", "confirm_custom_amount"}
