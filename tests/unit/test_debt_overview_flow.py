from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot.conversation_flows.debt_overview_flow import (
    build_main_keyboard,
    create_debt_overview_flow,
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
    api.debt_manager = SimpleNamespace(get_debt_summary_by_creditor=AsyncMock(return_value=summary))
    api.message_manager = SimpleNamespace(send_text=AsyncMock())
    return api


@pytest.mark.asyncio
async def test_build_main_keyboard_contains_creditor_buttons_and_close():
    summary = {
        "Charlie": {"total_owed": 3.0, "paypal_link": None, "debts": []},
        "Alice": {"total_owed": 5.0, "paypal_link": None, "debts": []},
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
async def test_custom_amount_input_distributes_payment_oldest_first():
    debts = [FakeDebt(total_amount=5.0, paid_amount=0.0), FakeDebt(total_amount=4.0, paid_amount=1.0)]
    summary = {"Alice": {"total_owed": 8.0, "paypal_link": None, "debts": debts}}
    api = make_api(summary)
    flow_state = MessageFlowState(
        current_state_id="custom_amount",
        flow_data={"selected_creditor_name": "Alice", "debt_summary": summary},
    )

    next_state = await handle_custom_amount_input("6", flow_state, api, 123)

    assert next_state == "main"
    assert debts[0].paid_amount == 5.0
    assert debts[1].paid_amount == 2.0
    api.message_manager.send_text.assert_awaited_once()


def test_create_debt_overview_flow_contains_expected_states():
    flow = create_debt_overview_flow()
    assert set(flow.states.keys()) == {"main", "creditor_payment", "custom_amount"}
