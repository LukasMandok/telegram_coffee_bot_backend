"""
Example: Credit Overview using MessageFlow System

This demonstrates how to refactor the credit_overview_conversation
to use the new declarative MessageFlow system.
"""

from typing import Any, Dict, List, Optional
import re
from .message_flow import MessageFlow, MessageDefinition, ButtonCallback, MessageAction, NotificationStyle, StateType
from telethon import Button


def parse_money_input(input_text: str) -> Optional[float]:
    """
    Parse various money input formats into a float.
    
    Supports formats like:
    - 0.2, .2, 0,2, ,2
    - 0.2€, .2€, 0,2€, ,2€
    - 0.2 €, .2 €, etc.
    
    Returns:
        float: Parsed amount or None if invalid
    """
    # Remove spaces and euro symbol
    cleaned = input_text.strip().replace(' ', '').replace('€', '')
    
    # Replace comma with dot for decimal separator
    cleaned = cleaned.replace(',', '.')
    
    # Handle leading dot (e.g., ".5" -> "0.5")
    if cleaned.startswith('.'):
        cleaned = '0' + cleaned
    
    try:
        amount = float(cleaned)
        return amount if amount >= 0 else None
    except ValueError:
        return None


async def build_credit_main_text(flow_state, api, user_id) -> str:
    """Build the main credit overview text."""
    user = await api.conversation_manager.repo.find_user_by_id(user_id)
    all_credits = await api.debt_manager.get_user_credits(user, include_settled=False)
    
    if not all_credits:
        return "✅ **No Outstanding Credits**\n\nNo one owes you money! 🎉"
    
    # Group credits by card
    card_summaries = {}
    total_all_credits = 0.0
    
    for debt in all_credits:
        card_name = debt.coffee_card.name if debt.coffee_card else "Unknown Card"
        debtor_name = debt.debtor.display_name if debt.debtor else "Unknown"
        outstanding = debt.total_amount - debt.paid_amount
        
        if card_name not in card_summaries:
            card_summaries[card_name] = {"debtors": {}, "total": 0.0}
        if debtor_name not in card_summaries[card_name]["debtors"]:
            card_summaries[card_name]["debtors"][debtor_name] = 0.0
        
        card_summaries[card_name]["debtors"][debtor_name] += outstanding
        card_summaries[card_name]["total"] += outstanding
        total_all_credits += outstanding
    
    # Build text
    text = "💰 **Your Credit Overview**\n\n"
    text += "People owe you money from these coffee cards:\n\n"
    
    for card_name, summary in card_summaries.items():
        text += f"**{card_name}**\n"
        for debtor_name, amount in summary["debtors"].items():
            text += f"  • {debtor_name}: €{amount:.2f}\n"
        text += f"  **Subtotal: €{summary['total']:.2f}**\n\n"
    
    text += f"**Total Owed to You: €{total_all_credits:.2f}**"
    
    return text


async def build_credit_main_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
    """Build the main credit overview keyboard."""
    return [
        [ButtonCallback("💸 Mark as Paid", "mark_paid"), ButtonCallback("📢 Notify All", "notify_all")],
        [ButtonCallback("❌ Close", "close")]
    ]


async def handle_notify_all(flow_state, api, user_id) -> str:
    """Handle the 'Notify All Debtors' action."""
    user = await api.conversation_manager.repo.find_user_by_id(user_id)
    all_credits = await api.debt_manager.get_user_credits(user, include_settled=False)
    
    notified_count = 0
    for debt in all_credits:
        if debt.debtor and hasattr(debt.debtor, 'user_id'):
            outstanding = debt.total_amount - debt.paid_amount
            card_name = debt.coffee_card.name if debt.coffee_card else "Unknown Card"
            creditor_name = user.display_name
            
            notify_text = (
                f"💳 **Payment Reminder**\n\n"
                f"You owe **€{outstanding:.2f}** to {creditor_name}\n"
                f"from coffee card: **{card_name}**\n\n"
            )
            
            if getattr(user, 'paypal_link', None):
                payment_link = f"{user.paypal_link}/{outstanding:.2f}EUR"
                notify_text += f"💳 Pay now: {payment_link}"
            
            try:
                await api.message_manager.send_text(
                    debt.debtor.user_id,
                    notify_text,
                    vanish=True,
                    conv=True
                )
                notified_count += 1
            except Exception:
                pass
    
    # Store notification result in flow_data for notification state
    flow_state.flow_data['notification_result'] = f"✅ Sent {notified_count} payment reminder(s)"
    
    # Return to main menu via notification state
    return "notification_sent"


async def build_notification_text(flow_state, api, user_id) -> str:
    """Build notification confirmation text."""
    return flow_state.flow_data.get('notification_result', '✅ Notifications sent')


async def build_debtors_list_text(flow_state, api, user_id) -> str:
    """Build the debtors selection text."""
    user = await api.conversation_manager.repo.find_user_by_id(user_id)
    all_credits = await api.debt_manager.get_user_credits(user, include_settled=False)
    
    # Group by debtor
    debtor_totals = {}
    for debt in all_credits:
        debtor_name = debt.debtor.display_name if debt.debtor else "Unknown"
        outstanding = debt.total_amount - debt.paid_amount
        if debtor_name not in debtor_totals:
            debtor_totals[debtor_name] = 0.0
        debtor_totals[debtor_name] += outstanding
    
    text = "**Select a debtor to mark payments:**\n\n"
    for debtor_name, total in debtor_totals.items():
        text += f"• {debtor_name}: €{total:.2f}\n"
    
    return text


async def build_debtors_list_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
    """Build the debtors selection keyboard - two debtors per row."""
    user = await api.conversation_manager.repo.find_user_by_id(user_id)
    all_credits = await api.debt_manager.get_user_credits(user, include_settled=False)
    
    # Group by debtor
    debtor_totals = {}
    for debt in all_credits:
        debtor_name = debt.debtor.display_name if debt.debtor else "Unknown"
        outstanding = debt.total_amount - debt.paid_amount
        if debtor_name not in debtor_totals:
            debtor_totals[debtor_name] = 0.0
        debtor_totals[debtor_name] += outstanding
    
    # Create buttons, two per row
    buttons = []
    row = []
    for debtor_name in sorted(debtor_totals.keys()):
        row.append(ButtonCallback(
            f"{debtor_name} (€{debtor_totals[debtor_name]:.2f})",
            f"debtor:{debtor_name}"
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    
    # Add remaining button if odd number of debtors
    if row:
        buttons.append(row)
    
    buttons.append([ButtonCallback("◀ Back", "back")])
    return buttons


async def handle_debtor_selection(data: str, flow_state, api, user_id) -> Optional[str]:
    """Handle debtor selection and store in flow_data."""
    if data.startswith("debtor:"):
        debtor_name = data.split(":", 1)[1]
        flow_state.flow_data['selected_debtor'] = debtor_name
        # Store original debts state for undo functionality
        flow_state.flow_data['original_payments'] = {}
        return "debtor_debts"
    return None


async def build_debtor_debts_text(flow_state, api, user_id) -> str:
    """Build text for individual debtor's debts."""
    debtor_name = flow_state.flow_data.get('selected_debtor', 'Unknown')
    user = await api.conversation_manager.repo.find_user_by_id(user_id)
    all_credits = await api.debt_manager.get_user_credits(user, include_settled=False)
    
    # Filter debts for this debtor
    debtor_debts = {}
    total_owed = 0.0
    
    for debt in all_credits:
        if debt.debtor and debt.debtor.display_name == debtor_name:
            outstanding = debt.total_amount - debt.paid_amount
            if outstanding > 0:
                card_name = debt.coffee_card.name if debt.coffee_card else "Unknown Card"
                debt_id = str(debt.id)
                debtor_debts[debt_id] = {
                    'card_name': card_name,
                    'amount': outstanding,
                    'debt': debt
                }
                total_owed += outstanding
    
    # Store debts in flow_data for later use
    flow_state.flow_data['debtor_debts'] = debtor_debts
    
    # Check if any payments have been staged
    staged_payments = flow_state.flow_data.get('staged_payments', {})
    total_staged = sum(staged_payments.values())
    
    text = f"**Payments from {debtor_name}**\n\n"
    text += f"Total owed: **€{total_owed:.2f}**\n"
    
    if total_staged > 0:
        text += f"Staged payments: **€{total_staged:.2f}**\n"
        text += f"Remaining: **€{total_owed - total_staged:.2f}**"
    
    return text


async def build_debtor_debts_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
    """Build keyboard for debtor's debts - two cards per row."""
    debtor_debts = flow_state.flow_data.get('debtor_debts', {})
    staged_payments = flow_state.flow_data.get('staged_payments', {})
    has_changes = bool(staged_payments)
    
    buttons = []
    row = []
    
    # Add button for each card (two per row) - show remaining amount after staged payments
    for debt_id, info in debtor_debts.items():
        card_name = info['card_name']
        original_amount = info['amount']
        staged_amount = staged_payments.get(debt_id, 0)
        remaining_amount = original_amount - staged_amount
        
        # Show remaining amount if partially paid
        if staged_amount > 0 and remaining_amount > 0:
            button_text = f"{card_name} (€{remaining_amount:.2f})"
        elif staged_amount > 0 and remaining_amount == 0:
            button_text = f"{card_name} ✓"
        else:
            button_text = f"{card_name} (€{original_amount:.2f})"
        
        row.append(ButtonCallback(
            button_text,
            f"pay_card:{debt_id}"
        ))
        
        if len(row) == 2:
            buttons.append(row)
            row = []
    
    # Add remaining button if odd number of cards
    if row:
        buttons.append(row)
    
    # Add "All Paid" button
    buttons.append([ButtonCallback("✅ Mark All as Paid", "pay_all")])
    
    # Add Undo and Save/Back buttons (dynamic based on changes)
    if has_changes:
        buttons.append([
            ButtonCallback("↩️ Undo", "undo"),
            ButtonCallback("💾 Save", "save")
        ])
    else:
        buttons.append([ButtonCallback("◀ Back", "back")])
    
    return buttons


async def handle_debtor_debts_button(data: str, flow_state, api, user_id) -> Optional[str]:
    """Handle button presses in debtor debts view."""
    if data.startswith("pay_card:"):
        # Mark specific card as paid
        debt_id = data.split(":", 1)[1]
        debtor_debts = flow_state.flow_data.get('debtor_debts', {})
        
        if debt_id in debtor_debts:
            debt_info = debtor_debts[debt_id]
            amount = debt_info['amount']
            
            # Stage the payment
            if 'staged_payments' not in flow_state.flow_data:
                flow_state.flow_data['staged_payments'] = {}
            
            flow_state.flow_data['staged_payments'][debt_id] = amount
        
        # Return None to stay on same state - will re-render via continue
        return None
    
    elif data == "pay_all":
        # Mark all debts as paid
        debtor_debts = flow_state.flow_data.get('debtor_debts', {})
        flow_state.flow_data['staged_payments'] = {
            debt_id: info['amount'] 
            for debt_id, info in debtor_debts.items()
        }
        return None  # Stay on same state
    
    elif data == "undo":
        # Clear all staged payments
        flow_state.flow_data['staged_payments'] = {}
        return None  # Stay on same state
    
    elif data == "save":
        # Apply all staged payments
        staged_payments = flow_state.flow_data.get('staged_payments', {})
        debtor_debts = flow_state.flow_data.get('debtor_debts', {})
        
        for debt_id, amount in staged_payments.items():
            if debt_id in debtor_debts:
                debt = debtor_debts[debt_id]['debt']
                await api.debt_manager._apply_payment_to_debt(debt, amount)
        
        # Clear staged payments and return to debtors list
        flow_state.flow_data['staged_payments'] = {}
        return "debtors_list"
    
    elif data == "back":
        # Go back to debtors list without saving
        flow_state.flow_data['staged_payments'] = {}
        return "debtors_list"
    
    return None


async def handle_debtor_debts_input(input_text: str, flow_state, api, user_id) -> Optional[str]:
    """Handle custom amount input for debtor debts."""
    print(f"[DEBUG] handle_debtor_debts_input called with input_text='{input_text}'")
    
    try:
        # Parse the money input
        custom_amount = parse_money_input(input_text)
        print(f"[DEBUG] Parsed amount: {custom_amount}")
        
        if custom_amount is None:
            # Invalid format - send error with auto-delete
            print(f"[DEBUG] Invalid amount, sending error")
            await api.message_manager.send_text(
                user_id,
                "❌ Invalid amount. Please enter a number.",
                vanish=True,
                conv=True,
                delete_after=2
            )
            return "debtor_debts"
        
        if custom_amount <= 0:
            # Send error notification with auto-delete
            print(f"[DEBUG] Amount <= 0, sending error")
            await api.message_manager.send_text(
                user_id,
                "❌ Amount must be positive",
                vanish=True,
                conv=True,
                delete_after=2
            )
            return "debtor_debts"
        
        debtor_debts = flow_state.flow_data.get('debtor_debts', {})
        print(f"[DEBUG] Retrieved debtor_debts: {len(debtor_debts)} debts")
        total_owed = sum(info['amount'] for info in debtor_debts.values())
        print(f"[DEBUG] Total owed: {total_owed}")
    
        if custom_amount > total_owed:
            print(f"[DEBUG] Amount {custom_amount} > total_owed {total_owed}, sending error")
            await api.message_manager.send_text(
                user_id,
                f"❌ Amount cannot exceed total owed (€{total_owed:.2f})",
                vanish=True,
                conv=True,
                delete_after=2
            )
            return "debtor_debts"
        
        # Get existing staged payments or create new dict
        staged_payments = flow_state.flow_data.get('staged_payments', {}).copy()
        print(f"[DEBUG] Existing staged_payments: {staged_payments}")
        
        # Apply custom amount to debts (oldest first)
        remaining = custom_amount
        
        # Sort debts by creation date (oldest first)
        sorted_debts = sorted(
            debtor_debts.items(),
            key=lambda x: x[1]['debt'].created_at
        )
        
        # Distribute the custom amount across debts
        distributed_amounts = {}
        for debt_id, info in sorted_debts:
            if remaining <= 0:
                break
            
            debt_amount = info['amount']
            # Check how much is already staged for this debt
            already_staged = staged_payments.get(debt_id, 0)
            remaining_debt = debt_amount - already_staged
            
            if remaining_debt > 0:
                payment = min(remaining, remaining_debt)
                distributed_amounts[debt_id] = already_staged + payment
                remaining -= payment
        
        # Update staged payments with the new amounts
        staged_payments.update(distributed_amounts)
        flow_state.flow_data['staged_payments'] = staged_payments
    
        print(f"[DEBUG] Distributed amounts: {distributed_amounts}")
        print(f"[DEBUG] Updated staged_payments: {staged_payments}")
        print(f"[DEBUG] Returning 'debtor_debts' to re-render")
        
        # Send confirmation message
        cards_affected = len(distributed_amounts)
        await api.message_manager.send_text(
            user_id,
            f"✅ Staged €{custom_amount:.2f} payment across {cards_affected} card(s)",
            vanish=True,
            conv=True,
            delete_after=2
        )
        
        return "debtor_debts"
    except Exception as e:
        print(f"[ERROR] Exception in handle_debtor_debts_input: {e}")
        import traceback
        traceback.print_exc()
        await api.message_manager.send_text(
            user_id,
            f"❌ Error processing payment: {str(e)}",
            vanish=True,
            conv=True,
            delete_after=3
        )
        return "debtor_debts"


def create_credit_flow() -> MessageFlow:
    """
    Create the credit overview message flow.
    
    This demonstrates the declarative approach to building menu flows.
    """
    flow = MessageFlow()
    
    # Main credit overview
    flow.add_state(MessageDefinition(
        state_id="main",
        text_builder=build_credit_main_text,
        keyboard_builder=build_credit_main_keyboard,
        action=MessageAction.AUTO,
        timeout=180,
        next_state_map={
            "mark_paid": "debtors_list",
            "notify_all": "notification_sent"
        },
        exit_buttons=["close"],
        on_button_press=handle_notify_all_button
    ))
    
    # Notification confirmation (transient state)
    flow.add_state(MessageDefinition(
        state_id="notification_sent",
        text_builder=build_notification_text,
        buttons=[[ButtonCallback("◀ Back to Credits", "back")]],
        action=MessageAction.EDIT,
        timeout=30,
        next_state_map={"back": "main"},
        notification_style=NotificationStyle.MESSAGE_TEMP,
        notification_auto_delete=3
    ))
    
    # Debtors list
    flow.add_state(MessageDefinition(
        state_id="debtors_list",
        text_builder=build_debtors_list_text,
        keyboard_builder=build_debtors_list_keyboard,
        action=MessageAction.EDIT,
        timeout=120,
        next_state_map={"back": "main"},
        on_button_press=handle_debtor_selection
    ))
    
    # Individual debtor's debts - MIXED state for text input + buttons
    flow.add_state(MessageDefinition(
        state_id="debtor_debts",
        state_type=StateType.MIXED,
        text_builder=build_debtor_debts_text,
        keyboard_builder=build_debtor_debts_keyboard,
        action=MessageAction.EDIT,
        timeout=120,
        input_prompt="Mark individual cards as paid or enter custom amount:",
        input_storage_key="custom_amount_input",  # Don't overwrite debtor_debts data
        on_input_received=handle_debtor_debts_input,
        on_button_press=handle_debtor_debts_button,
        on_timeout=handle_debtor_timeout
    ))
    
    return flow


async def handle_debtor_timeout(flow_state, api, user_id):
    """Handle timeout in debtor_debts - undo any staged changes."""
    flow_state.flow_data['staged_payments'] = {}


async def handle_notify_all_button(data: str, flow_state, api, user_id) -> Optional[str]:
    """Handle button press, call notify_all if needed."""
    if data == "notify_all":
        return await handle_notify_all(flow_state, api, user_id)
    return None


# Usage in conversations.py:
# 
# @managed_conversation("credit_overview", 300)
# async def credit_overview_conversation(self, user_id: int, conv: Conversation, state: ConversationState) -> bool:
#     flow = create_credit_flow()
#     return await flow.run(conv, user_id, self.api, start_state="main")
