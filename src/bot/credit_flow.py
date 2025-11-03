"""
Refactored Credit Overview using MessageFlow Helpers

This demonstrates how the helpers reduce boilerplate significantly.
Compare this to credit_flow_example.py - much shorter and clearer!
"""

from typing import Optional, List
from .message_flow import (
    MessageFlow, MessageDefinition, ButtonCallback, 
    MessageAction, StateType, NotificationStyle
)
from .message_flow_helpers import (
    GridLayout, StagingManager, MoneyParser,
    ListBuilder, InputDistributor, make_state,
    NavigationButtons
)


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

async def build_credit_main_text(flow_state, api, user_id) -> str:
    """Build the main credit overview text - with automatic caching."""
    all_credits = await get_credits_data(flow_state, api, user_id)
    
    if not all_credits:
        return "✅ **No Outstanding Credits**\n\nNo one owes you money! 🎉"
    
    # Group credits by card
    groups = {}
    group_summaries = {}
    total = 0.0
    
    for debt in all_credits:
        card_name = debt.coffee_card.name if debt.coffee_card else "Unknown Card"
        debtor_name = debt.debtor.display_name if debt.debtor else "Unknown"
        outstanding = debt.total_amount - debt.paid_amount
        
        if card_name not in groups:
            groups[card_name] = []
        groups[card_name].append((debtor_name, f"{outstanding:.2f} €"))
        
        # Calculate group total
        current_total = sum(float(item[1].replace('€', '')) for item in groups[card_name])
        group_summaries[card_name] = f"Subtotal: {current_total:.2f} €"
        total += outstanding
    
    # Use ListBuilder for formatting
    builder = ListBuilder()
    return builder.build_grouped(
        title="💰 Your Credit Overview",
        groups=groups,
        group_summaries=group_summaries,
        overall_summary=f"Total Owed to You: {total:.2f} €",
        align_values=True  # Enable aligned formatting
    )


async def build_credit_main_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
    """Build the main credit overview keyboard."""
    return [
        [ButtonCallback("💸 Mark as Paid", "mark_paid"), ButtonCallback("📢 Notify All", "notify_all")],
        NavigationButtons.close()
    ]


async def handle_main_buttons(data: str, flow_state, api, user_id) -> Optional[str]:
    """Handle main menu buttons."""
    if data == "notify_all":
        # Send notifications
        user = await api.conversation_manager.repo.find_user_by_id(user_id)
        all_credits = await get_credits_data(flow_state, api, user_id)
        
        notified = 0
        for debt in all_credits:
            if debt.debtor and hasattr(debt.debtor, 'user_id'):
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
    
    # Store for later use
    flow_state.set('debtor_debts', debtor_debts)
    
    # Get staging info
    staging = StagingManager(flow_state, 'staged_payments')
    staged = staging.get_staged()
    total_staged = sum(staged.values())
    
    # Build text
    text = f"**Payments from {debtor_name}**\n\n"
    text += f"Total owed: **{total_owed:.2f} €**\n"
    
    if total_staged > 0:
        text += f"Staged payments: **{total_staged:.2f} €**\n"
        text += f"Remaining: **{(total_owed - total_staged):.2f} €**"
    
    return text


async def build_debtor_debts_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
    """Build keyboard for debtor's debts."""
    debtor_debts = flow_state.get('debtor_debts', {})
    staging = StagingManager(flow_state, 'staged_payments')
    staged = staging.get_staged()
    
    # Build items with remaining amounts
    items = []
    for debt_id, info in debtor_debts.items():
        card_name = info['card_name']
        original = info['amount']
        staged_amount = staged.get(debt_id, 0)
        remaining = original - staged_amount
        
        if staged_amount > 0 and remaining == 0:
            text = f"{card_name} ✓"
        elif staged_amount > 0:
            text = f"{card_name} ({remaining:.2f} €)"
        else:
            text = f"{card_name} ({original:.2f} €)"
        
        items.append((text, f"pay_card:{debt_id}"))
    
    # Use GridLayout
    grid = GridLayout(items_per_row=2)
    
    # Dynamic footer based on staging state
    if staging.has_changes():
        footer = [
            [ButtonCallback("✅ Mark All as Paid", "pay_all")],
            NavigationButtons.undo_and_save()
        ]
    else:
        footer = [
            [ButtonCallback("✅ Mark All as Paid", "pay_all")],
            NavigationButtons.back()
        ]
    
    return grid.build(items=items, footer_buttons=footer)


async def handle_debtor_debts_button(data: str, flow_state, api, user_id) -> Optional[str]:
    """Handle button presses in debtor debts view."""
    staging = StagingManager(flow_state, 'staged_payments')
    debtor_debts = flow_state.get('debtor_debts', {})
    
    if data.startswith("pay_card:"):
        debt_id = data.split(":", 1)[1]
        if debt_id in debtor_debts:
            staging.stage(debt_id, debtor_debts[debt_id]['amount'])
        return None  # Stay on same state
    
    elif data == "pay_all":
        for debt_id, info in debtor_debts.items():
            staging.stage(debt_id, info['amount'])
        return None
    
    elif data == "undo":
        staging.clear()
        return None
    
    elif data == "save":
        # Commit staged payments
        async def apply_payment(debt_id: str, amount: float):
            if debt_id in debtor_debts:
                debt = debtor_debts[debt_id]['debt']
                await api.debt_manager._apply_payment_to_debt(debt, amount)
        
        await staging.commit(apply_payment)
        return "debtors_list"
    
    elif data == "back":
        staging.clear()
        return "debtors_list"
    
    return None


async def handle_debtor_debts_input(input_text: str, flow_state, api, user_id) -> Optional[str]:
    """Handle custom amount input for debtor debts."""
    # Parse amount
    parser = MoneyParser()
    amount = parser.parse(input_text)
    
    if amount is None or amount <= 0:
        await api.message_manager.send_text(
            user_id, "❌ Invalid amount. Please enter a positive number.",
            vanish=True, conv=True, delete_after=2
        )
        return "debtor_debts"
    
    # Get debts
    debtor_debts = flow_state.get('debtor_debts', {})
    total_owed = sum(info['amount'] for info in debtor_debts.values())
    
    if amount > total_owed:
        await api.message_manager.send_text(
            user_id, f"❌ Amount cannot exceed total owed ({total_owed:.2f} €)",
            vanish=True, conv=True, delete_after=2
        )
        return "debtor_debts"
    
    # Distribute amount using helper
    staging = StagingManager(flow_state, 'staged_payments')
    distributor = InputDistributor()
    
    # Convert debtor_debts to simple dict for distributor
    items = {debt_id: info['amount'] for debt_id, info in debtor_debts.items()}
    
    # Distribute, sorting by creation date (oldest first)
    distributed = distributor.distribute(
        amount=amount,
        items=items,
        existing=staging.get_staged(),
        sort_key=lambda x: debtor_debts[x[0]]['debt'].created_at
    )
    
    # Update staging
    for debt_id, new_total in distributed.items():
        staging.stage(debt_id, new_total)
    
    # Send confirmation
    await api.message_manager.send_text(
        user_id,
        f"✅ Staged {amount:.2f} € payment across {len(distributed)} card(s)",
        vanish=True, conv=True, delete_after=2
    )
    
    return "debtor_debts"


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
