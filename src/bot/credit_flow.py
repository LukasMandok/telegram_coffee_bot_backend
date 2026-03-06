"""
Refactored Credit Overview using MessageFlow Helpers

This demonstrates how the helpers reduce boilerplate significantly.
Compare this to credit_flow_example.py - much shorter and clearer!
"""

# NOTE: what is pervious_on_save? and shouldnt this be just the default behaviour. I dont really understand, where something else would be needed.

from typing import Optional, List

from ..models.beanie_models import TelegramUser
from .message_flow import (
    MessageFlow, MessageDefinition, ButtonCallback, 
    MessageAction, StateType, NotificationStyle
)
from .message_flow_helpers import (
    GridLayout, ListBuilder, make_state,
    NavigationButtons
)
from .payment_flow import (
    build_staged_payments_keyboard,
    get_total_staged,
    handle_staged_payments_button,
    handle_staged_payments_input,
)


VIEW_MODE_KEY = "credit_overview_view_mode"
VIEW_BY_CARD = "by_card"
VIEW_BY_DEBTOR = "by_debtor"


def _get_view_mode(flow_state) -> str:
    mode = flow_state.get(VIEW_MODE_KEY, VIEW_BY_CARD)
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
    if not flow_state.has('credits_raw'):
        user = await api.conversation_manager.repo.find_user_by_id(user_id)
        all_credits = await api.debt_manager.get_user_credits(user, include_settled=False)
        flow_state.set('credits_raw', all_credits)
    return flow_state.get('credits_raw')


def invalidate_credit_cache(flow_state) -> None:
    flow_state.clear('credits_raw', 'debtor_debts')

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

            groups[debtor_name].append((card_name, f"{outstanding:.2f} €"))
            group_totals[debtor_name] += outstanding
            group_summaries[debtor_name] = f"Subtotal: {group_totals[debtor_name]:.2f} €"
            total += outstanding
    else:
        for debt in all_credits:
            card_name = debt.coffee_card.name if debt.coffee_card else "Unknown Card"
            debtor_name = debt.debtor.display_name if debt.debtor else "Unknown"
            outstanding = debt.total_amount - debt.paid_amount

            if card_name not in groups:
                groups[card_name] = []
                group_totals[card_name] = 0.0

            groups[card_name].append((debtor_name, f"{outstanding:.2f} €"))
            group_totals[card_name] += outstanding
            group_summaries[card_name] = f"Subtotal: {group_totals[card_name]:.2f} €"
            total += outstanding
    
    # Use ListBuilder for formatting
    builder = ListBuilder()
    title = "💰 Your Credit Overview (By Debtor)" if mode == VIEW_BY_DEBTOR else "💰 Your Credit Overview"
    return builder.build_grouped(
        title=title,
        groups=groups,
        group_summaries=group_summaries,
        overall_summary=f"Total Owed to You: {total:.2f} €",
        align_values=True  # Enable aligned formatting
    )


async def build_credit_main_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
    """Build the main credit overview keyboard."""
    toggle_label = "👥 View by User" if _get_view_mode(flow_state) == VIEW_BY_CARD else "🗂️ View by Card"
    return [
        [ButtonCallback(toggle_label, "toggle_view")],
        [ButtonCallback("💸 Mark as Paid", "mark_paid"), ButtonCallback("📢 Notify All", "notify_all")],
        NavigationButtons.close()
    ]


async def handle_main_buttons(data: str, flow_state, api, user_id) -> Optional[str]:
    """Handle main menu buttons."""
    if data == "toggle_view":
        current_mode = _get_view_mode(flow_state)
        flow_state.set(VIEW_MODE_KEY, VIEW_BY_DEBTOR if current_mode == VIEW_BY_CARD else VIEW_BY_CARD)
        return None

    if data == "notify_all":
        # Send notifications
        user = await api.conversation_manager.repo.find_user_by_id(user_id)
        all_credits = await get_credits_data(flow_state, api, user_id)
        
        notified = 0
        for debt in all_credits:
            if isinstance(debt.debtor, TelegramUser):
                outstanding = debt.total_amount - debt.paid_amount
                card_name = debt.coffee_card.name if debt.coffee_card else "Unknown Card"
                
                notify_text = (
                    f"💳 **Payment Reminder**\n\n"
                    f"You owe **{outstanding:.2f} €** to {user.display_name}\n"
                    f"from coffee card: **{card_name}**"
                )
                
                if getattr(user, 'paypal_link', None):
                    notify_text += f"\n\n💳 Pay now: {user.paypal_link}/{outstanding:.2f}EUR"
                
                try:
                    await api.message_manager.send_text(debt.debtor.user_id, notify_text, vanish=False, conv=False)
                    notified += 1
                except:
                    pass
        
        flow_state.flow_data['notification_result'] = f"✅ Sent {notified} payment reminder(s)"
        return "notification_sent"
    
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
    items = [(name, f"{total:.2f} €") for name, total in debtor_totals.items()]
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
    items = [(f"{name} ({total:.2f} €)", f"debtor:{name}") for name, total in sorted(debtor_totals.items())]

    return grid.build(
        items=items,
        footer_buttons=[NavigationButtons.back()]
    )


async def handle_debtor_selection(data: str, flow_state, api, user_id) -> Optional[str]:
    """Handle debtor selection."""
    if data.startswith("debtor:"):
        debtor_name = data.split(":", 1)[1]
        flow_state.set('selected_debtor', debtor_name)
        return "debtor_debts"
    return None


# ============================================================================
# DEBTOR DEBTS (with staging)
# ============================================================================

async def build_debtor_debts_text(flow_state, api, user_id) -> str:
    """Build text for individual debtor's debts."""
    debtor_name = flow_state.get('selected_debtor', 'Unknown')
    
    # Get debts for this debtor
    all_credits = await get_credits_data(flow_state, api, user_id)
    
    debtor_debts = {}
    total_owed = 0.0
    
    for debt in all_credits:
        if debt.debtor and debt.debtor.display_name == debtor_name:
            outstanding = debt.total_amount - debt.paid_amount
            if outstanding > 0:
                debt_id = str(debt.id)
                debtor_debts[debt_id] = {
                    'card_name': debt.coffee_card.name if debt.coffee_card else "Unknown",
                    'amount': outstanding,
                    'debt': debt
                }
                total_owed += outstanding
    
    flow_state.set('debtor_debts', debtor_debts)
    total_staged = get_total_staged(flow_state)
    
    # Build text
    text = f"**Payments from {debtor_name}**\n\n"
    text += f"Total owed: **{total_owed:.2f} €**\n"
    
    if total_staged > 0:
        text += f"Staged payments: **{total_staged:.2f} €**\n"
        text += f"Remaining: **{(total_owed - total_staged):.2f} €**"
    
    return text


async def build_debtor_debts_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
    """Build keyboard for debtor's debts."""
    return await build_staged_payments_keyboard(
        flow_state,
        api,
        user_id,
        items_key='debtor_debts',
        pay_all_text="✅ Mark All as Paid",
    )


async def handle_debtor_debts_button(data: str, flow_state, api, user_id) -> Optional[str]:
    """Handle button presses in debtor debts view."""
    async def apply_payment(debt, amount: float):
        await api.debt_manager._apply_payment_to_debt(debt, amount)

    async def snapshot_before_commit() -> None:
        snapshot_manager = api.get_snapshot_manager()
        await snapshot_manager.create_snapshot(
            reason="Apply Payment (credit menu)",
            context="apply_payment_credit_menu",
            collections=("user_debts", "payments"),
            save_in_background=True,
        )

    async def after_save(_total_staged: float) -> None:
        debtor_name = flow_state.get('selected_debtor', 'selected debtor')
        await api.message_manager.send_text(
            user_id,
            f"✅ Marked {_total_staged:.2f} € as paid by {debtor_name}",
            vanish=True,
            conv=True,
            delete_after=2,
        )
        invalidate_credit_cache(flow_state)

    return await handle_staged_payments_button(
        data,
        flow_state,
        api,
        user_id,
        items_key='debtor_debts',
        on_apply_payment=apply_payment,
        save_state='debtors_list',
        back_state='debtors_list',
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
        items_key='debtor_debts',
        current_state='debtor_debts',
    )


# ============================================================================
# FLOW DEFINITION
# ============================================================================

def create_credit_flow() -> MessageFlow:
    """Create the credit overview message flow - much simpler with helpers!"""
    flow = MessageFlow()
    
    # Main overview
    flow.add_state(make_state(
        "main",
        text_builder=build_credit_main_text,
        keyboard_builder=build_credit_main_keyboard,
        action=MessageAction.AUTO,
        timeout=180,
        keep_message_on_exit=False,
        next_state_map={"mark_paid": "debtors_list"},
        exit_buttons=["close"],
        on_button_press=handle_main_buttons,
    ))
    
    flow.add_state(make_state(
        "notification_sent",
        text_builder=lambda fs, api, uid: fs.flow_data.get('notification_result', '✅ Notifications sent'),
        buttons=[[ButtonCallback("◀ Back to Credits", "back")]],
        action=MessageAction.EDIT,
        timeout=30,
        next_state_map={"back": "main"},
    ))
    
    # Debtors list
    flow.add_state(make_state(
        "debtors_list",
        text_builder=build_debtors_list_text,
        keyboard_builder=build_debtors_list_keyboard,
        action=MessageAction.EDIT,
        timeout=120,
        next_state_map={"back": "main"},
        on_button_press=handle_debtor_selection,
    ))
    
    # Individual debtor's debts
    flow.add_state(make_state(
        "debtor_debts",
        text_builder=build_debtor_debts_text,
        keyboard_builder=build_debtor_debts_keyboard,
        action=MessageAction.EDIT,
        timeout=120,
        input_prompt="Mark individual cards as paid or enter custom amount:",
        input_storage_key="custom_amount_input",
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

    flow = create_credit_flow()
    return await flow.run(conv, user_id, api, start_state="main")
