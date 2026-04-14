"""Shared staged payment flow utilities used by debt and credit overviews."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional

from .message_flow import ButtonCallback
from .message_flow_helpers import CommonCallbacks, GridLayout, InputDistributor, MoneyParser, NavigationButtons, StagingManager


MONEY_PARSER = MoneyParser()


def get_total_owed(items: Dict[str, Dict[str, Any]]) -> float:
    return sum(float(info["amount"]) for info in items.values())


def get_total_staged(flow_state, *, staging_key: str = "staged_payments") -> float:
    stagingManager = StagingManager(flow_state, staging_key)
    return sum(float(value) for value in stagingManager.get_staged().values())


async def build_staged_payments_keyboard(
    flow_state,
    api,
    user_id,
    *,
    items_key: str,
    staging_key: str = "staged_payments",
    items_per_row: int = 2,
    pay_all_text: str = "✅ Mark All as Paid",
) -> List[List[ButtonCallback]]:
    itemsById = flow_state.get(items_key, {})
    stagingManager = StagingManager(flow_state, staging_key)
    staged = stagingManager.get_staged()

    items = []
    for item_id, info in itemsById.items():
        card_name = info["card_name"]
        original = float(info["amount"])
        staged_amount = float(staged.get(item_id, 0.0))
        remaining = original - staged_amount

        if staged_amount > 0 and remaining <= 0:
            text = f"{card_name} ✓"
        elif staged_amount > 0:
            text = f"{card_name} ({remaining:.2f} €)"
        else:
            text = f"{card_name} ({original:.2f} €)"

        items.append((text, f"pay_card:{item_id}"))

    grid = GridLayout(items_per_row=items_per_row)
    if stagingManager.has_changes():
        footer = [
            [ButtonCallback(pay_all_text, "pay_all")],
            NavigationButtons.undo_and_save(save_text="✅ Commit", save_callback="commit"),
        ]
    else:
        footer = [[ButtonCallback(pay_all_text, "pay_all")], NavigationButtons.back()]

    return grid.build(items=items, footer_buttons=footer)


async def handle_staged_payments_button(
    data: str,
    flow_state,
    api,
    user_id,
    *,
    items_key: str,
    on_apply_payment: Callable[[Any, float], Awaitable[None]],
    save_state: str,
    back_state: str,
    staging_key: str = "staged_payments",
    on_before_commit: Optional[Callable[[], Awaitable[None]]] = None,
    on_after_save: Optional[Callable[[float], Awaitable[None]]] = None,
    return_previous_on_save: bool = False,
) -> Optional[str]:
    itemsById = flow_state.get(items_key, {})
    stagingManager = StagingManager(flow_state, staging_key)

    if data.startswith("pay_card:"):
        item_id = data.split(":", 1)[1]
        if item_id in itemsById:
            staged = stagingManager.get_staged()
            if float(staged.get(item_id, 0.0)) > 0:
                stagingManager.unstage(item_id)
            else:
                stagingManager.stage(item_id, float(itemsById[item_id]["amount"]))
        return None

    if data == "pay_all":
        for item_id, info in itemsById.items():
            stagingManager.stage(item_id, float(info["amount"]))
        return None

    if data == CommonCallbacks.UNDO:
        stagingManager.clear()
        return None

    if data in ("commit", CommonCallbacks.SAVE):
        staged = stagingManager.get_staged()
        total_staged = sum(float(value) for value in staged.values())
        api.logger.debug(
            f"[PaymentFlow] commit requested: staged_count={len(staged)}, total_staged={total_staged:.2f}"
        )

        if not staged:
            await api.message_manager.send_text(
                user_id,
                "ℹ️ No staged payments to commit.",
                vanish=True,
                conv=True,
                delete_after=2,
            )
            api.logger.debug("[PaymentFlow] commit ignored: no staged payments")
            return None

        async def commit_one(item_id: str, amount: float):
            if item_id in itemsById:
                await on_apply_payment(itemsById[item_id]["debt"], amount)

        try:
            await stagingManager.commit(commit_one, pre_commit=on_before_commit)
            api.logger.debug("[PaymentFlow] commit finished successfully")
            if on_after_save:
                await on_after_save(total_staged)
            if return_previous_on_save and flow_state.previous_state_id:
                return flow_state.previous_state_id
            return save_state
        except Exception as exc:
            api.logger.debug(f"[PaymentFlow] commit failed: {exc}")
            await api.message_manager.send_text(
                user_id,
                f"❌ Commit failed: {exc}",
                vanish=True,
                conv=True,
                delete_after=4,
            )
            return None

    if data == CommonCallbacks.BACK:
        stagingManager.clear()
        return back_state

    return None


async def handle_staged_payments_input(
    input_text: str,
    flow_state,
    api,
    user_id,
    *,
    items_key: str,
    current_state: str,
    staging_key: str = "staged_payments",
) -> Optional[str]:
    amount = MONEY_PARSER.parse(input_text)
    if amount is None or amount <= 0:
        await api.message_manager.send_text(
            user_id,
            "❌ Invalid amount. Please enter a positive number.",
            vanish=True,
            conv=True,
            delete_after=2,
        )
        return current_state

    itemsById = flow_state.get(items_key, {})
    total_owed = get_total_owed(itemsById)
    if amount > total_owed:
        await api.message_manager.send_text(
            user_id,
            f"❌ Amount cannot exceed total owed ({total_owed:.2f} €)",
            vanish=True,
            conv=True,
            delete_after=2,
        )
        return current_state

    stagingManager = StagingManager(flow_state, staging_key)
    distributor = InputDistributor()
    itemAmounts = {item_id: float(info["amount"]) for item_id, info in itemsById.items()}

    distributed = distributor.distribute(
        amount=amount,
        items=itemAmounts,
        existing=stagingManager.get_staged(),
        sort_key=lambda entry: itemsById[entry[0]]["debt"].created_at,
    )

    for item_id, new_total in distributed.items():
        stagingManager.stage(item_id, float(new_total))

    await api.message_manager.send_text(
        user_id,
        f"✅ Staged {amount:.2f} € payment across {len(distributed)} card(s)",
        vanish=True,
        conv=True,
        delete_after=2,
    )

    return current_state
