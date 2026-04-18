"""Debt conversation flow (MessageFlow-based)."""

from __future__ import annotations

from typing import Dict, List, Optional

from ...models.beanie_models import TelegramUser
from ...models.coffee_models import PaymentReason
from ..message_flow import ButtonCallback, MessageAction, MessageFlow
from ..message_flow_helpers import (
    CommonCallbacks,
    GridLayout,
    ListBuilder,
    NavigationButtons,
    format_money,
    make_state,
)
from ..payment_flow import (
    build_staged_payments_keyboard,
    get_total_owed,
    get_total_staged,
    handle_staged_payments_button,
    handle_staged_payments_input,
)


STATE_MAIN = "main"
STATE_CREDITOR_DEBTS = "creditor_debts"

CB_CREDITOR_PREFIX = "creditor:"

KEY_DEBT_USER = "debt_user"
KEY_DEBTS_RAW = "debts_raw"
KEY_CREDITOR_SUMMARY = "creditor_summary"
KEY_CREDITOR_DEBTS = "creditor_debts"
KEY_SELECTED_CREDITOR = "selected_creditor"

KEY_STAGED_PAYMENTS = "staged_payments"
KEY_CUSTOM_AMOUNT_INPUT = "custom_amount_input"


def invalidate_debt_cache(flow_state) -> None:
    flow_state.clear(KEY_DEBT_USER, KEY_DEBTS_RAW, KEY_CREDITOR_SUMMARY, KEY_CREDITOR_DEBTS)


async def get_debt_user(flow_state, api, user_id):
    if not flow_state.has(KEY_DEBT_USER):
        user = await api.conversation_manager.repo.find_user_by_id(user_id)
        flow_state.set(KEY_DEBT_USER, user)
    return flow_state.get(KEY_DEBT_USER)


async def get_debts_data(flow_state, api, user_id):
    if not flow_state.has(KEY_DEBTS_RAW):
        user = await get_debt_user(flow_state, api, user_id)
        if not user:
            return []
        debts = await api.debt_manager.get_user_debts(user, include_settled=False)
        flow_state.set(KEY_DEBTS_RAW, debts)
    return flow_state.get(KEY_DEBTS_RAW, [])


async def get_creditor_summary(flow_state, api, user_id) -> Dict[str, Dict]:
    if flow_state.has(KEY_CREDITOR_SUMMARY):
        return flow_state.get(KEY_CREDITOR_SUMMARY)

    debts = await get_debts_data(flow_state, api, user_id)
    summary: Dict[str, Dict] = {}

    for debt in debts:
        outstanding = max(0.0, debt.total_amount - debt.paid_amount)
        if outstanding <= 0:
            continue

        if not debt.creditor:
            continue

        creditor_name = debt.creditor.display_name
        if creditor_name not in summary:
            summary[creditor_name] = {
                "total_owed": 0.0,
                "paypal_link": debt.creditor.paypal_link,
                "creditor": debt.creditor,
                "debts": [],
            }

        summary[creditor_name]["total_owed"] += outstanding
        summary[creditor_name]["debts"].append(debt)

    flow_state.set(KEY_CREDITOR_SUMMARY, summary)
    return summary


async def build_main_text(flow_state, api, user_id) -> str:
    user = await get_debt_user(flow_state, api, user_id)
    if not user:
        return "❌ User not found."

    creditor_summary = await get_creditor_summary(flow_state, api, user_id)
    if not creditor_summary:
        return "✅ **No Outstanding Debts**\n\nYou don't owe anyone money! 🎉"

    items = []
    for creditor_name in sorted(creditor_summary.keys()):
        total_owed = creditor_summary[creditor_name]["total_owed"]
        items.append((creditor_name, format_money(total_owed)))

    builder = ListBuilder()
    total_all = sum(info["total_owed"] for info in creditor_summary.values())
    return builder.build(
        title="💳 Your Debt Overview",
        items=items,
        summary=f"Total Outstanding: {format_money(total_all)}",
        align_values=True,
    )


async def build_main_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
    creditor_summary = await get_creditor_summary(flow_state, api, user_id)
    if not creditor_summary:
        return []

    grid = GridLayout(items_per_row=2)
    creditor_items = []
    for creditor_name in sorted(creditor_summary.keys()):
        total_owed = creditor_summary[creditor_name]["total_owed"]
        creditor_items.append((f"💰 {creditor_name} ({format_money(total_owed)})", f"creditor:{creditor_name}"))

    return grid.build(items=creditor_items, footer_buttons=[NavigationButtons.close()])


async def handle_main_button(data: str, flow_state, api, user_id) -> Optional[str]:
    if data.startswith(CB_CREDITOR_PREFIX):
        creditor_name = data.split(":", 1)[1]
        flow_state.set(KEY_SELECTED_CREDITOR, creditor_name)
        flow_state.pop(KEY_STAGED_PAYMENTS, None)
        return STATE_CREDITOR_DEBTS
    return None


async def build_creditor_debts_text(flow_state, api, user_id) -> str:
    creditor_name = flow_state.get(KEY_SELECTED_CREDITOR, "Unknown")
    creditor_summary = await get_creditor_summary(flow_state, api, user_id)

    if creditor_name not in creditor_summary:
        return "❌ Creditor not found."

    creditor_info = creditor_summary[creditor_name]
    debts = creditor_info["debts"]

    creditor_debts: Dict[str, Dict] = {}
    card_items: List[tuple[str, str]] = []

    for debt in debts:
        outstanding = max(0.0, debt.total_amount - debt.paid_amount)
        if outstanding <= 0:
            continue

        debt_id = str(debt.id)
        card_name = debt.coffee_card.name if debt.coffee_card else "Unknown Card"
        creditor_debts[debt_id] = {
            "card_name": card_name,
            "amount": outstanding,
            "debt": debt,
        }
        card_items.append((card_name, format_money(outstanding)))

    flow_state.set(KEY_CREDITOR_DEBTS, creditor_debts)

    total_owed = get_total_owed(creditor_debts)
    total_staged = get_total_staged(flow_state)

    builder = ListBuilder()
    summary = f"Total owed: {format_money(total_owed)}"
    if total_staged > 0:
        summary += (
            f"\nStaged: {format_money(total_staged)}"
            f"\nRemaining: {format_money(total_owed - total_staged)}"
        )

    text = builder.build(
        title=f"Payments to {creditor_name}",
        items=card_items,
        summary=summary,
        empty_message="No open card debts.",
        align_values=True,
    )

    paypal_link = creditor_info["paypal_link"]
    if paypal_link:
        text += f"\n\n💳 Pay now: {paypal_link}/{total_owed:.2f}EUR"

    return text


async def build_creditor_debts_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
    return await build_staged_payments_keyboard(
        flow_state,
        api,
        user_id,
        items_key=KEY_CREDITOR_DEBTS,
        pay_all_text="✅ Mark All as Paid",
    )


async def handle_creditor_debts_button(data: str, flow_state, api, user_id) -> Optional[str]:
    async def apply_payment(debt, amount: float):
        await api.debt_manager._apply_payment_to_debt(
            debt,
            amount,
            reason=PaymentReason.DEBTOR_MARKED_PAID,
        )

    async def snapshot_before_commit() -> None:
        snapshot_manager = api.get_snapshot_manager()
        await snapshot_manager.create_snapshot(
            reason="Apply Payment (debt menu)",
            context="apply_payment_debt_menu",
            collections=("user_debts", "payments"),
            save_in_background=True,
        )

    async def after_save(total_staged: float) -> None:
        debt_user = await get_debt_user(flow_state, api, user_id)
        creditor_name = flow_state.get(KEY_SELECTED_CREDITOR, "selected creditor")

        await api.message_manager.send_text(
            user_id,
            f"✅ Marked {format_money(total_staged)} as paid to {creditor_name}",
            vanish=True,
            conv=True,
            delete_after=2,
        )

        if debt_user:
            creditor_summary = await get_creditor_summary(flow_state, api, user_id)
            creditor_info = creditor_summary.get(creditor_name)
            creditor_user = creditor_info["creditor"] if creditor_info else None

            if isinstance(creditor_user, TelegramUser) and creditor_user.user_id != user_id:
                updated_debts = await api.debt_manager.get_user_debts(debt_user, include_settled=False)
                remaining_owed = 0.0
                for debt in updated_debts:
                    if debt.creditor and debt.creditor.stable_id == creditor_user.stable_id:
                        remaining_owed += max(0.0, debt.total_amount - debt.paid_amount)

                await api.message_manager.send_user_notification(
                    creditor_user.user_id,
                    (
                        f"💸 **Payment update**\n\n"
                        f"{debt_user.display_name} marked **{format_money(total_staged)}** as paid to you.\n"
                        f"Remaining owed by {debt_user.display_name}: **{format_money(remaining_owed)}**"
                    ),
                )

        invalidate_debt_cache(flow_state)

    return await handle_staged_payments_button(
        data,
        flow_state,
        api,
        user_id,
        items_key=KEY_CREDITOR_DEBTS,
        on_apply_payment=apply_payment,
        save_state=STATE_MAIN,
        back_state=STATE_MAIN,
        on_before_commit=snapshot_before_commit,
        on_after_save=after_save,
        return_previous_on_save=True,
    )


async def handle_creditor_debts_input(input_text: str, flow_state, api, user_id) -> Optional[str]:
    return await handle_staged_payments_input(
        input_text,
        flow_state,
        api,
        user_id,
        items_key=KEY_CREDITOR_DEBTS,
        current_state=STATE_CREDITOR_DEBTS,
    )


def create_debt_flow() -> MessageFlow:
    flow = MessageFlow()

    creditor_debts_defaults = {
        KEY_CREDITOR_DEBTS: {},
        KEY_STAGED_PAYMENTS: {},
    }

    flow.add_state(
        make_state(
            STATE_MAIN,
            text_builder=build_main_text,
            keyboard_builder=build_main_keyboard,
            action=MessageAction.AUTO,
            timeout=180,
            keep_message_on_exit=False,
            exit_buttons=[CommonCallbacks.CLOSE],
            on_button_press=handle_main_button,
        )
    )

    flow.add_state(
        make_state(
            STATE_CREDITOR_DEBTS,
            text_builder=build_creditor_debts_text,
            keyboard_builder=build_creditor_debts_keyboard,
            action=MessageAction.EDIT,
            timeout=120,
            defaults=creditor_debts_defaults,
            input_prompt="Mark individual cards as paid or enter custom amount:",
            input_storage_key=KEY_CUSTOM_AMOUNT_INPUT,
            on_input_received=handle_creditor_debts_input,
            on_button_press=handle_creditor_debts_button,
        )
    )

    return flow


async def run_debt_flow(conv, user_id: int, api) -> bool:
    user = await api.conversation_manager.repo.find_user_by_id(user_id)
    if not user:
        await api.message_manager.send_text(
            user_id,
            "❌ User not found.",
            True,
            True,
        )
        return False

    api.logger.trace(
        f"DebtFlow start: user_id={user_id}, display_name={user.display_name}, stable_id={user.stable_id}, doc_id={user.id}"
    )

    debts = await api.debt_manager.get_user_debts(user, include_settled=False)
    api.logger.trace(f"DebtFlow precheck fetched debts={len(debts)}")

    has_outstanding_debt = any(
        debt.total_amount - debt.paid_amount > 0
        for debt in debts
    )

    if not has_outstanding_debt:
        await api.message_manager.send_text(
            user_id,
            "✅ **No Outstanding Debts**\n\nYou don't owe anyone money! 🎉",
            True,
            True,
        )
        return True

    flow = create_debt_flow()
    return await flow.run(conv, user_id, api, start_state=STATE_MAIN)
