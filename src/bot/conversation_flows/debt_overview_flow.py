"""Debt overview conversation flow (MessageFlow-based)."""

from __future__ import annotations

from typing import List, Optional

from ..message_flow import ButtonCallback, MessageDefinition, MessageFlow, StateType
from ..message_flow_helpers import GridLayout, NavigationButtons


async def get_debt_user(flow_state, api, user_id):
    """Fetch and cache the debt user."""
    if not flow_state.has("debt_user"):
        user = await api.conversation_manager.repo.find_user_by_id(user_id)
        flow_state.set("debt_user", user)
    return flow_state.get("debt_user")


async def get_debt_summary(flow_state, api, user_id):
    """Fetch and cache debt summary for the current user."""
    user = await get_debt_user(flow_state, api, user_id)
    if not user:
        return None

    if not flow_state.has("debt_summary"):
        debt_summary = await api.debt_manager.get_debt_summary_by_creditor(user)
        flow_state.set("debt_summary", debt_summary)
    return flow_state.get("debt_summary")


def invalidate_debt_summary_cache(flow_state) -> None:
    flow_state.pop("debt_summary", None)


async def build_main_text(flow_state, api, user_id) -> str:
    user = await get_debt_user(flow_state, api, user_id)
    if not user:
        return "❌ User not found."

    debt_summary = await get_debt_summary(flow_state, api, user_id)
    if not debt_summary:
        return "✅ **No Outstanding Debts**\n\nYou don't owe anyone money! 🎉"

    overview_text = "💳 **Your Debt Overview**\n\n"
    total_all_debts = 0.0

    for creditor_name, summary in debt_summary.items():
        total_owed = summary["total_owed"]
        total_all_debts += total_owed

        overview_text += f"**{creditor_name}**\n"
        overview_text += f"💰 You owe: **€{total_owed:.2f}**\n"

        if summary["paypal_link"]:
            payment_link_with_amount = f"{summary['paypal_link']}/{total_owed:.2f}EUR"
            overview_text += f"💳 Pay now: {payment_link_with_amount}\n"

        overview_text += "\n"

    overview_text += f"**Total Outstanding: €{total_all_debts:.2f}**\n\n"
    overview_text += "━━━━━━━━━━━━━━━━\n"
    overview_text += "**Mark as Paid:**\n"
    overview_text += "Select a creditor below to mark payment"
    return overview_text


async def build_main_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
    debt_summary = await get_debt_summary(flow_state, api, user_id)
    if not debt_summary:
        return [NavigationButtons.close()]

    grid = GridLayout(items_per_row=2)
    creditor_items = [
        (f"💰 {creditor_name}", f"debt_pay:{creditor_name}")
        for creditor_name in sorted(debt_summary.keys())
    ]
    return grid.build(items=creditor_items, footer_buttons=[NavigationButtons.close()])


async def handle_main_button(data: str, flow_state, api, user_id) -> Optional[str]:
    if data.startswith("debt_pay:"):
        creditor_name = data.split(":", 1)[1]
        debt_summary = await get_debt_summary(flow_state, api, user_id)
        if not debt_summary or creditor_name not in debt_summary:
            await api.message_manager.send_text(user_id, "❌ Creditor not found.", True, True)
            invalidate_debt_summary_cache(flow_state)
            return "main"

        flow_state.set("selected_creditor_name", creditor_name)
        return "creditor_payment"

    return None


async def build_creditor_payment_text(flow_state, api, user_id) -> str:
    creditor_name = flow_state.get("selected_creditor_name")
    debt_summary = await get_debt_summary(flow_state, api, user_id)

    if not debt_summary or creditor_name not in debt_summary:
        return "❌ Creditor not found."

    total_owed = debt_summary[creditor_name]["total_owed"]
    return (
        f"💳 **Mark Payment to {creditor_name}**\n\n"
        f"Total owed: **€{total_owed:.2f}**\n\n"
        "Choose an option:"
    )


async def build_creditor_payment_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
    creditor_name = flow_state.get("selected_creditor_name")
    debt_summary = await get_debt_summary(flow_state, api, user_id)
    total_owed = 0.0
    if debt_summary and creditor_name in debt_summary:
        total_owed = debt_summary[creditor_name]["total_owed"]

    return [
        [ButtonCallback(f"✅ Mark Full Amount (€{total_owed:.2f})", "mark_full")],
        [ButtonCallback("💵 Specify Custom Amount", "mark_custom")],
        NavigationButtons.back(),
    ]


async def _mark_full_amount(creditor_info) -> None:
    for debt in creditor_info["debts"]:
        total_due = debt.total_amount + getattr(debt, "debt_correction", 0.0)
        remaining = total_due - debt.paid_amount
        if remaining > 0:
            debt.paid_amount = total_due
            await debt.save()


async def _mark_custom_amount(creditor_info, paid_amount: float) -> None:
    remaining_to_pay = paid_amount
    for debt in creditor_info["debts"]:
        total_due = debt.total_amount + getattr(debt, "debt_correction", 0.0)
        debt_remaining = total_due - debt.paid_amount
        if debt_remaining > 0 and remaining_to_pay > 0:
            payment_for_this_debt = min(remaining_to_pay, debt_remaining)
            debt.paid_amount += payment_for_this_debt
            await debt.save()
            remaining_to_pay -= payment_for_this_debt


async def handle_creditor_payment_button(data: str, flow_state, api, user_id) -> Optional[str]:
    creditor_name = flow_state.get("selected_creditor_name")
    debt_summary = await get_debt_summary(flow_state, api, user_id)

    if data == "back":
        return "main"

    if not debt_summary or creditor_name not in debt_summary:
        await api.message_manager.send_text(user_id, "❌ Creditor not found.", True, True)
        invalidate_debt_summary_cache(flow_state)
        return "main"

    creditor_info = debt_summary[creditor_name]
    total_owed = creditor_info["total_owed"]

    if data == "mark_full":
        await _mark_full_amount(creditor_info)
        await api.message_manager.send_text(
            user_id,
            f"✅ Marked €{total_owed:.2f} as paid to {creditor_name}",
            True,
            True,
        )
        invalidate_debt_summary_cache(flow_state)
        return "main"

    if data == "mark_custom":
        return "custom_amount"

    return None


async def build_custom_amount_text(flow_state, api, user_id) -> str:
    creditor_name = flow_state.get("selected_creditor_name", "Unknown")
    debt_summary = await get_debt_summary(flow_state, api, user_id)
    total_owed = 0.0
    if debt_summary and creditor_name in debt_summary:
        total_owed = debt_summary[creditor_name]["total_owed"]

    error = flow_state.pop("custom_amount_error", None)
    error_text = f"❌ {error}\n\n" if error else ""

    return (
        f"{error_text}"
        f"💵 **Custom Payment to {creditor_name}**\n\n"
        f"Total owed: **€{total_owed:.2f}**\n\n"
        "Enter the amount you paid (in €):"
    )


async def handle_custom_amount_input(input_text: str, flow_state, api, user_id) -> Optional[str]:
    creditor_name = flow_state.get("selected_creditor_name")
    debt_summary = await get_debt_summary(flow_state, api, user_id)

    if not debt_summary or creditor_name not in debt_summary:
        await api.message_manager.send_text(user_id, "❌ Creditor not found.", True, True)
        invalidate_debt_summary_cache(flow_state)
        return "main"

    creditor_info = debt_summary[creditor_name]
    total_owed = creditor_info["total_owed"]

    try:
        paid_amount = float(input_text.strip())
        if paid_amount <= 0:
            raise ValueError("Amount must be positive")
        if paid_amount > total_owed:
            raise ValueError("Amount cannot exceed total owed")

        await _mark_custom_amount(creditor_info, paid_amount)
        await api.message_manager.send_text(
            user_id,
            f"✅ Marked €{paid_amount:.2f} as paid to {creditor_name}",
            True,
            True,
        )
        invalidate_debt_summary_cache(flow_state)
        return "main"
    except ValueError as exc:
        flow_state.set("custom_amount_error", f"Invalid amount: {exc}")
        return "custom_amount"


async def handle_custom_amount_button(data: str, flow_state, api, user_id) -> Optional[str]:
    if data == "back":
        return "creditor_payment"
    return None


def create_debt_overview_flow() -> MessageFlow:
    flow = MessageFlow()

    flow.add_state(
        MessageDefinition(
            state_id="main",
            text_builder=build_main_text,
            keyboard_builder=build_main_keyboard,
            timeout=180,
            exit_buttons=["close"],
            on_button_press=handle_main_button,
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id="creditor_payment",
            text_builder=build_creditor_payment_text,
            keyboard_builder=build_creditor_payment_keyboard,
            timeout=120,
            next_state_map={"back": "main"},
            on_button_press=handle_creditor_payment_button,
        )
    )

    flow.add_state(
        MessageDefinition(
            state_id="custom_amount",
            state_type=StateType.MIXED,
            text_builder=build_custom_amount_text,
            buttons=[NavigationButtons.back()],
            input_timeout=60,
            on_input_received=handle_custom_amount_input,
            on_button_press=handle_custom_amount_button,
            exit_buttons=[],
        )
    )

    return flow
