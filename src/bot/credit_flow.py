"""
Refactored Credit Overview using MessageFlow Helpers

This demonstrates how the helpers reduce boilerplate significantly.
Compare this to credit_flow_example.py - much shorter and clearer!
"""

# NOTE: what is pervious_on_save? and shouldnt this be just the default behaviour. I dont really understand, where something else would be needed.

from typing import Optional, List

from ..models.beanie_models import TelegramUser
from .message_flow import (
    MessageFlow, ButtonCallback,
    MessageAction,
)
from .message_flow_helpers import (
    GridLayout, ListBuilder, make_state,
    CommonCallbacks,
    NavigationButtons,
    format_money,
)
from .payment_flow import (
    build_staged_payments_keyboard,
    get_total_staged,
    handle_staged_payments_button,
    handle_staged_payments_input,
)
from ..models.coffee_models import PaymentReason
from .message_flow_ids import DebtQuickConfirmCallbacks


EPS = 1e-9


STATE_MAIN = "main"
STATE_NOTIFICATION_SENT = "notification_sent"
STATE_NOTIFY_MENU = "notify_menu"
STATE_NOTIFY_USERS = "notify_users"
STATE_DEBTORS_LIST = "debtors_list"
STATE_DEBTOR_DEBTS = "debtor_debts"

CB_TOGGLE_VIEW = "toggle_view"
CB_NOTIFY_MENU = "notify_menu"
CB_NOTIFY_ALL = "notify_all"
CB_NOTIFY_USERS = "notify_users"
CB_MARK_PAID = "mark_paid"

CB_NOTIFY_USER_PREFIX = "notify_user:"

CB_DEBTOR_PREFIX = "debtor:"

KEY_VIEW_MODE = "credit_overview_view_mode"
VIEW_BY_CARD = "by_card"
VIEW_BY_DEBTOR = "by_debtor"

KEY_CREDITS_RAW = "credits_raw"
KEY_CREDITOR_NAME = "creditor_name"
KEY_CREDITOR_PAYPAL_LINK = "creditor_paypal_link"
KEY_DEBTOR_DEBTS = "debtor_debts"
KEY_SELECTED_DEBTOR = "selected_debtor"
KEY_SELECTED_DEBTOR_TELEGRAM_USER_ID = "selected_debtor_telegram_user_id"
KEY_SELECTED_DEBTOR_TOTAL_OWED = "selected_debtor_total_owed"
KEY_NOTIFICATION_RESULT = "notification_result"
KEY_STAGED_PAYMENTS = "staged_payments"
KEY_CUSTOM_AMOUNT_INPUT = "custom_amount_input"
KEY_NOTIFIED_USER_IDS = "notified_user_ids"


def _get_view_mode(flow_state) -> str:
    mode = flow_state.get(KEY_VIEW_MODE, VIEW_BY_CARD)
    if mode not in (VIEW_BY_CARD, VIEW_BY_DEBTOR):
        return VIEW_BY_CARD
    return mode


# NOTE: it seams like the staging manager is created quite often. is this really necessary?
# shouldnt this be put directly into the create flow function with a dedicated message flow functionallity, which creates you the staging manager

# ============================================================================
# MAIN OVERVIEW
# ============================================================================

async def get_credits_data(flow_state, api, user_id):
    """Fetch and cache all credits for the user (raw objects), avoiding duplicate DB calls."""
    if not flow_state.has(KEY_CREDITS_RAW):
        user = await api.conversation_manager.repo.find_user_by_id(user_id)
        all_credits = await api.debt_manager.get_user_credits(user, include_settled=False)
        flow_state.set(KEY_CREDITS_RAW, all_credits)
        flow_state.set(KEY_CREDITOR_NAME, user.display_name or str(user_id))
        flow_state.set(KEY_CREDITOR_PAYPAL_LINK, user.paypal_link)
    return flow_state.get(KEY_CREDITS_RAW)


def invalidate_credit_cache(flow_state) -> None:
    flow_state.clear(
        KEY_CREDITS_RAW,
        KEY_DEBTOR_DEBTS,
        KEY_SELECTED_DEBTOR_TELEGRAM_USER_ID,
        KEY_SELECTED_DEBTOR_TOTAL_OWED,
    )

async def build_credit_main_text(flow_state, api, user_id) -> str:
    """Build the main credit overview text - with automatic caching."""
    all_credits = await get_credits_data(flow_state, api, user_id)
    
    if not all_credits:
        return "✅ **No Outstanding Credits**\n\nNo one owes you money! 🎉"
    
    mode = _get_view_mode(flow_state)
    groups = {}
    group_totals = {}
    group_summaries = {}
    total = 0.0

    if mode == VIEW_BY_DEBTOR:
        for debt in all_credits:
            debtor_name = debt.debtor.display_name if debt.debtor else "Unknown"
            card_name = debt.coffee_card.name if debt.coffee_card else "Unknown Card"
            outstanding = debt.total_amount - debt.paid_amount

            if debtor_name not in groups:
                groups[debtor_name] = []
                group_totals[debtor_name] = 0.0

            groups[debtor_name].append((card_name, format_money(outstanding)))
            group_totals[debtor_name] += outstanding
            group_summaries[debtor_name] = f"Subtotal: {format_money(group_totals[debtor_name])}"
            total += outstanding
    else:
        for debt in all_credits:
            card_name = debt.coffee_card.name if debt.coffee_card else "Unknown Card"
            debtor_name = debt.debtor.display_name if debt.debtor else "Unknown"
            outstanding = debt.total_amount - debt.paid_amount

            if card_name not in groups:
                groups[card_name] = []
                group_totals[card_name] = 0.0

            groups[card_name].append((debtor_name, format_money(outstanding)))
            group_totals[card_name] += outstanding
            group_summaries[card_name] = f"Subtotal: {format_money(group_totals[card_name])}"
            total += outstanding
    
    # Use ListBuilder for formatting
    builder = ListBuilder()
    title = "💰 Your Credit Overview (By Debtor)" if mode == VIEW_BY_DEBTOR else "💰 Your Credit Overview"
    return builder.build_grouped(
        title=title,
        groups=groups,
        group_summaries=group_summaries,
        overall_summary=f"Total Owed to You: {format_money(total)}",
        align_values=True  # Enable aligned formatting
    )


async def build_credit_main_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
    """Build the main credit overview keyboard."""
    toggle_label = "👥 View by User" if _get_view_mode(flow_state) == VIEW_BY_CARD else "🗂️ View by Card"
    return [
        [ButtonCallback(toggle_label, CB_TOGGLE_VIEW)],
        [ButtonCallback("💸 Mark as Paid", CB_MARK_PAID), ButtonCallback("📢 Notify", CB_NOTIFY_MENU)],
        NavigationButtons.close()
    ]


async def handle_main_buttons(data: str, flow_state, api, user_id) -> Optional[str]:
    """Handle main menu buttons."""
    if data == CB_TOGGLE_VIEW:
        current_mode = _get_view_mode(flow_state)
        new_mode = VIEW_BY_DEBTOR if current_mode == VIEW_BY_CARD else VIEW_BY_CARD
        flow_state.set(KEY_VIEW_MODE, new_mode)
        await api.conversation_manager.repo.update_user_settings(
            user_id,
            credit_overview_view_mode=new_mode,
        )
        return None
    
    return None


async def _send_debt_reminder_with_quick_confirm(
    api,
    *,
    debtor_user_id: int,
    creditor_name: str,
    creditor_paypal_link: str | None,
    debt,
) -> bool:
    outstanding = debt.total_amount - debt.paid_amount
    if outstanding <= EPS:
        return False

    card_name = debt.coffee_card.name if debt.coffee_card else "Unknown Card"
    notify_text = (
        "💳 **Payment Reminder**\n\n"
        f"You owe **{format_money(outstanding)}** to {creditor_name}\n"
        f"from coffee card: **{card_name}**"
    )
    if creditor_paypal_link:
        notify_text += f"\n\n💳 Pay now: {creditor_paypal_link}/{outstanding:.2f}EUR"

    reminder = await api.message_manager.send_user_notification(
        debtor_user_id,
        notify_text,
    )
    if reminder is None:
        return False

    await api.message_manager.send_user_notification_keyboard(
        debtor_user_id,
        DebtQuickConfirmCallbacks.QUESTION_TEXT,
        buttons=[
            [
                ButtonCallback(
                    DebtQuickConfirmCallbacks.YES_TEXT,
                    f"{DebtQuickConfirmCallbacks.YES_PREFIX}{debtor_user_id}:{str(debt.id)}",
                ),
                ButtonCallback(
                    DebtQuickConfirmCallbacks.NO_TEXT,
                    f"{DebtQuickConfirmCallbacks.NO_PREFIX}{debtor_user_id}:{str(debt.id)}",
                ),
            ]
        ],
    )

    return True


async def build_notify_menu_text(flow_state, api, user_id) -> str:
    return "📢 **Notify**\n\nChoose who to notify:"


async def build_notify_menu_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
    return [
        [ButtonCallback("📢 Notify All", CB_NOTIFY_ALL), ButtonCallback("👤 Notify Users", CB_NOTIFY_USERS)],
        NavigationButtons.back(),
    ]


async def handle_notify_menu_buttons(data: str, flow_state, api, user_id) -> Optional[str]:
    if data == CB_NOTIFY_USERS:
        return STATE_NOTIFY_USERS

    if data != CB_NOTIFY_ALL:
        return None

    all_credits = await get_credits_data(flow_state, api, user_id)
    creditor_name = flow_state.get(KEY_CREDITOR_NAME, str(user_id))
    creditor_paypal_link = flow_state.get(KEY_CREDITOR_PAYPAL_LINK)

    notified = 0
    for debt in all_credits:
        if not isinstance(debt.debtor, TelegramUser):
            continue
        try:
            sent = await _send_debt_reminder_with_quick_confirm(
                api,
                debtor_user_id=debt.debtor.user_id,
                creditor_name=creditor_name,
                creditor_paypal_link=creditor_paypal_link,
                debt=debt,
            )
            if sent:
                notified += 1
        except Exception:
            pass

    flow_state.flow_data[KEY_NOTIFICATION_RESULT] = f"✅ Sent {notified} payment reminder(s)"
    return STATE_NOTIFICATION_SENT


async def build_notify_users_text(flow_state, api, user_id) -> str:
    return "Select users to notify (tap to send):"


async def build_notify_users_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
    all_credits = await get_credits_data(flow_state, api, user_id)

    totals: dict[int, tuple[str, float]] = {}
    for debt in all_credits:
        if not isinstance(debt.debtor, TelegramUser):
            continue
        outstanding = debt.total_amount - debt.paid_amount
        if outstanding <= EPS:
            continue

        debtor_id = debt.debtor.user_id
        debtor_name = debt.debtor.display_name or str(debtor_id)
        prev_name, prev_total = totals.get(debtor_id, (debtor_name, 0.0))
        totals[debtor_id] = (prev_name, prev_total + float(outstanding))

    notified_ids = set(flow_state.get(KEY_NOTIFIED_USER_IDS, []))
    items = [
        (
            f"{'✅ ' if debtor_id in notified_ids else ''}{name} ({format_money(total)})",
            f"{CB_NOTIFY_USER_PREFIX}{debtor_id}",
        )
        for debtor_id, (name, total) in sorted(totals.items(), key=lambda it: it[1][0].lower())
    ]

    grid = GridLayout(items_per_row=2)
    return grid.build(items=items, footer_buttons=[NavigationButtons.back()])


async def handle_notify_users_buttons(data: str, flow_state, api, user_id) -> Optional[str]:
    if not data.startswith(CB_NOTIFY_USER_PREFIX):
        return None

    debtor_id_str = data.split(":", 1)[1]
    try:
        debtor_id = int(debtor_id_str)
    except ValueError:
        return None

    notified_list = list(flow_state.get(KEY_NOTIFIED_USER_IDS, []))
    if debtor_id in notified_list:
        return None

    all_credits = await get_credits_data(flow_state, api, user_id)
    creditor_name = flow_state.get(KEY_CREDITOR_NAME, str(user_id))
    creditor_paypal_link = flow_state.get(KEY_CREDITOR_PAYPAL_LINK)

    sent_any = False
    for debt in all_credits:
        if not isinstance(debt.debtor, TelegramUser):
            continue
        if debt.debtor.user_id != debtor_id:
            continue
        try:
            sent = await _send_debt_reminder_with_quick_confirm(
                api,
                debtor_user_id=debtor_id,
                creditor_name=creditor_name,
                creditor_paypal_link=creditor_paypal_link,
                debt=debt,
            )
            if sent:
                sent_any = True
        except Exception:
            pass

    if sent_any:
        notified_list.append(debtor_id)
        flow_state.set(KEY_NOTIFIED_USER_IDS, notified_list)
    return None


# ============================================================================
# DEBTORS LIST
# ============================================================================

async def build_debtors_list_text(flow_state, api, user_id) -> str:
    """Build the debtors selection text."""
    # Reuse cached raw credits data
    all_credits = await get_credits_data(flow_state, api, user_id)
    
    # Group by debtor
    debtor_totals = {}
    for debt in all_credits:
        debtor_name = debt.debtor.display_name if debt.debtor else "Unknown"
        outstanding = debt.total_amount - debt.paid_amount
        debtor_totals[debtor_name] = debtor_totals.get(debtor_name, 0.0) + outstanding
    
    # Use ListBuilder
    builder = ListBuilder()
    items = [(name, format_money(total)) for name, total in debtor_totals.items()]
    return builder.build(
        title="Select a debtor to mark payments:",
        items=items,
        align_values=True  # Enable aligned formatting
    )


async def build_debtors_list_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
    """Build the debtors selection keyboard - two debtors per row using GridLayout."""
    all_credits = await get_credits_data(flow_state, api, user_id)
    
    # Group by debtor
    debtor_totals = {}
    for debt in all_credits:
        debtor_name = debt.debtor.display_name if debt.debtor else "Unknown"
        outstanding = debt.total_amount - debt.paid_amount
        debtor_totals[debtor_name] = debtor_totals.get(debtor_name, 0.0) + outstanding
    
    # Use GridLayout helper
    grid = GridLayout(items_per_row=2)
    items = [(f"{name} ({format_money(total)})", f"debtor:{name}") for name, total in sorted(debtor_totals.items())]

    return grid.build(
        items=items,
        footer_buttons=[NavigationButtons.back()]
    )


async def handle_debtor_selection(data: str, flow_state, api, user_id) -> Optional[str]:
    """Handle debtor selection."""
    if data.startswith(CB_DEBTOR_PREFIX):
        debtor_name = data.split(":", 1)[1]
        flow_state.set(KEY_SELECTED_DEBTOR, debtor_name)
        return STATE_DEBTOR_DEBTS
    return None


# ============================================================================
# DEBTOR DEBTS (with staging)
# ============================================================================

async def build_debtor_debts_text(flow_state, api, user_id) -> str:
    """Build text for individual debtor's debts."""
    debtor_name = flow_state.get(KEY_SELECTED_DEBTOR, 'Unknown')
    
    # Get debts for this debtor
    all_credits = await get_credits_data(flow_state, api, user_id)
    
    debtor_debts = {}
    total_owed = 0.0
    debtor_telegram_user_id: int | None = None
    
    for debt in all_credits:
        if debt.debtor and debt.debtor.display_name == debtor_name:
            if debtor_telegram_user_id is None and isinstance(debt.debtor, TelegramUser):
                debtor_telegram_user_id = debt.debtor.user_id
            outstanding = debt.total_amount - debt.paid_amount
            if outstanding > 0:
                debt_id = str(debt.id)
                debtor_debts[debt_id] = {
                    'card_name': debt.coffee_card.name if debt.coffee_card else "Unknown",
                    'amount': outstanding,
                    'debt': debt
                }
                total_owed += outstanding
    
    flow_state.set(KEY_DEBTOR_DEBTS, debtor_debts)
    flow_state.set(KEY_SELECTED_DEBTOR_TELEGRAM_USER_ID, debtor_telegram_user_id)
    flow_state.set(KEY_SELECTED_DEBTOR_TOTAL_OWED, total_owed)
    total_staged = get_total_staged(flow_state, staging_key=KEY_STAGED_PAYMENTS)
    
    # Build text
    text = f"**Payments from {debtor_name}**\n\n"
    text += f"Total owed: **{format_money(total_owed)}**\n"
    
    if total_staged > 0:
        text += f"Staged payments: **{format_money(total_staged)}**\n"
        text += f"Remaining: **{format_money(total_owed - total_staged)}**"
    
    return text


async def build_debtor_debts_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
    """Build keyboard for debtor's debts."""
    return await build_staged_payments_keyboard(
        flow_state,
        api,
        user_id,
        items_key=KEY_DEBTOR_DEBTS,
        staging_key=KEY_STAGED_PAYMENTS,
        pay_all_text="✅ Mark All as Paid",
    )


async def notify_debtors(
    api,
    *,
    creditor_user_id: int,
    creditor_name: str,
    debtor_telegram_user_id: int | None,
    paid_amount: float,
    remaining_owed: float,
) -> None:
    if debtor_telegram_user_id is None:
        return
    if debtor_telegram_user_id == creditor_user_id:
        return

    remaining_line = (
        f"✅ You don't owe any more money to {creditor_name}."
        if remaining_owed <= EPS
        else f"You still owe **{format_money(remaining_owed)}** to {creditor_name}."
    )

    await api.message_manager.send_user_notification(
        debtor_telegram_user_id,
        (
            "💸 **Payment update**\n\n"
            f"{creditor_name} marked **{format_money(paid_amount)}** as paid.\n"
            f"{remaining_line}"
        ),
    )


async def handle_debtor_debts_button(data: str, flow_state, api, user_id) -> Optional[str]:
    """Handle button presses in debtor debts view."""
    async def apply_payment(debt, amount: float):
        await api.debt_manager._apply_payment_to_debt(
            debt,
            amount,
            reason=PaymentReason.CREDITOR_MARKED_PAID,
        )

    async def snapshot_before_commit() -> None:
        snapshot_manager = api.get_snapshot_manager()
        await snapshot_manager.create_snapshot(
            reason="Apply Payment (credit menu)",
            context="apply_payment_credit_menu",
            collections=("user_debts", "payments"),
            save_in_background=True,
        )

    async def after_save(_total_staged: float) -> None:
        debtor_name = flow_state.get(KEY_SELECTED_DEBTOR, 'selected debtor')
        await api.message_manager.send_text(
            user_id,
            f"✅ Marked {format_money(_total_staged)} as paid by {debtor_name}",
            vanish=True,
            conv=True,
            delete_after=2,
        )

        # Notify the debtor (if they are a Telegram user) that the creditor marked payment as paid.
        try:
            await notify_debtors(
                api,
                creditor_user_id=user_id,
                creditor_name=flow_state.get(KEY_CREDITOR_NAME, str(user_id)),
                debtor_telegram_user_id=flow_state.get(KEY_SELECTED_DEBTOR_TELEGRAM_USER_ID),
                paid_amount=_total_staged,
                remaining_owed=max(
                    0.0,
                    float(flow_state.get(KEY_SELECTED_DEBTOR_TOTAL_OWED, 0.0)) - float(_total_staged),
                ),
            )
        except Exception:
            pass

        invalidate_credit_cache(flow_state)

    return await handle_staged_payments_button(
        data,
        flow_state,
        api,
        user_id,
        items_key=KEY_DEBTOR_DEBTS,
        on_apply_payment=apply_payment,
        save_state=STATE_DEBTORS_LIST,
        back_state=STATE_DEBTORS_LIST,
        staging_key=KEY_STAGED_PAYMENTS,
        on_before_commit=snapshot_before_commit,
        on_after_save=after_save,
        return_previous_on_save=True,
    )


async def handle_debtor_debts_input(input_text: str, flow_state, api, user_id) -> Optional[str]:
    """Handle custom amount input for debtor debts."""
    return await handle_staged_payments_input(
        input_text,
        flow_state,
        api,
        user_id,
        items_key=KEY_DEBTOR_DEBTS,
        current_state=STATE_DEBTOR_DEBTS,
        staging_key=KEY_STAGED_PAYMENTS,
    )


# ============================================================================
# FLOW DEFINITION
# ============================================================================

def create_credit_flow() -> MessageFlow:
    """Create the credit overview message flow - much simpler with helpers!"""
    flow = MessageFlow()

    main_defaults = {
        KEY_VIEW_MODE: VIEW_BY_CARD,
    }

    notify_users_defaults = {
        KEY_NOTIFIED_USER_IDS: [],
    }

    debtor_debts_defaults = {
        KEY_DEBTOR_DEBTS: {},
        KEY_STAGED_PAYMENTS: {},
    }
    
    # Main overview
    flow.add_state(make_state(
        STATE_MAIN,
        text_builder=build_credit_main_text,
        keyboard_builder=build_credit_main_keyboard,
        action=MessageAction.AUTO,
        timeout=180,
        keep_message_on_exit=False,
        defaults=main_defaults,
        next_state_map={CB_MARK_PAID: STATE_DEBTORS_LIST, CB_NOTIFY_MENU: STATE_NOTIFY_MENU},
        exit_buttons=[CommonCallbacks.CLOSE],
        on_button_press=handle_main_buttons,
    ))

    flow.add_state(make_state(
        STATE_NOTIFY_MENU,
        text_builder=build_notify_menu_text,
        keyboard_builder=build_notify_menu_keyboard,
        action=MessageAction.EDIT,
        timeout=120,
        next_state_map={CommonCallbacks.BACK: STATE_MAIN},
        on_button_press=handle_notify_menu_buttons,
    ))

    flow.add_state(make_state(
        STATE_NOTIFY_USERS,
        text_builder=build_notify_users_text,
        keyboard_builder=build_notify_users_keyboard,
        action=MessageAction.EDIT,
        timeout=180,
        defaults=notify_users_defaults,
        next_state_map={CommonCallbacks.BACK: STATE_NOTIFY_MENU},
        on_button_press=handle_notify_users_buttons,
    ))
    
    flow.add_state(make_state(
        STATE_NOTIFICATION_SENT,
        text_builder=lambda fs, api, uid: fs.flow_data.get(KEY_NOTIFICATION_RESULT, '✅ Notifications sent'),
        buttons=[[ButtonCallback("◀ Back to Credits", CommonCallbacks.BACK)]],
        action=MessageAction.EDIT,
        timeout=30,
        next_state_map={CommonCallbacks.BACK: STATE_MAIN},
    ))
    
    # Debtors list
    flow.add_state(make_state(
        STATE_DEBTORS_LIST,
        text_builder=build_debtors_list_text,
        keyboard_builder=build_debtors_list_keyboard,
        action=MessageAction.EDIT,
        timeout=120,
        next_state_map={CommonCallbacks.BACK: STATE_MAIN},
        on_button_press=handle_debtor_selection,
    ))
    
    # Individual debtor's debts
    flow.add_state(make_state(
        STATE_DEBTOR_DEBTS,
        text_builder=build_debtor_debts_text,
        keyboard_builder=build_debtor_debts_keyboard,
        action=MessageAction.EDIT,
        timeout=120,
        defaults=debtor_debts_defaults,
        input_prompt="Mark individual cards as paid or enter custom amount:",
        input_storage_key=KEY_CUSTOM_AMOUNT_INPUT,
        on_input_received=handle_debtor_debts_input,
        on_button_press=handle_debtor_debts_button,
    ))
    
    return flow

# NOTE: maybe it is actually better to keep this in the conversation manager, because this is not actually message flow, but regular messages

async def run_credit_flow(conv, user_id: int, api) -> bool:
    user = await api.conversation_manager.repo.find_user_by_id(user_id)
    if user is None:
        await api.message_manager.send_text(
            user_id,
            "❌ User not found.",
            True,
            True,
        )
        return False

    credits = await api.debt_manager.get_user_credits(user, include_settled=False)
    has_outstanding_credit = any(
        credit.total_amount - credit.paid_amount > 0
        for credit in credits
    )

    if not has_outstanding_credit:
        await api.message_manager.send_text(
            user_id,
            "✅ **No Outstanding Credits**\n\nNo one owes you money! 🎉",
            True,
            True,
        )
        return True

    user_settings = await api.conversation_manager.repo.get_user_settings(user_id)
    initial_view_mode = VIEW_BY_CARD
    if user_settings and user_settings.credit_overview_view_mode in (VIEW_BY_CARD, VIEW_BY_DEBTOR):
        initial_view_mode = user_settings.credit_overview_view_mode

    flow = create_credit_flow()
    return await flow.run(
        conv,
        user_id,
        api,
        start_state=STATE_MAIN,
        initial_data={KEY_VIEW_MODE: initial_view_mode},
    )
