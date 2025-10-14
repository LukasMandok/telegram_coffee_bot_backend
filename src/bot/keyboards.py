"""
Telegram Keyboard Management

This module handles the generation of inline keyboards for the coffee ordering bot.
It provides various keyboard layouts for different bot interactions like confirmation,
group coffee ordering, and pagination.
"""

from typing import Any, List, TYPE_CHECKING
from telethon import Button, events
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ..api.telethon_api import GroupState


class KeyboardButton(BaseModel):
    """Represents a keyboard button configuration."""
    text: str = Field(..., description="Button display text")
    callback_data: str = Field(..., description="Data sent when button is pressed")
    row: int = Field(default=0, ge=0, description="Button row position")


class KeyboardManager:
    """
    Manages all keyboard layouts and interactions for the Telegram bot.
    
    This class generates various types of inline keyboards used throughout
    the coffee ordering bot, including confirmation dialogs, group ordering
    interfaces, and paginated displays.
    """
    
    @staticmethod
    def get_confirmation_keyboard() -> Any:
        """
        Generate a simple Yes/No confirmation keyboard.
        
        Returns:
            List of button rows containing Yes and No buttons
        """
        return [
            [  
                Button.inline("Yes", b"Yes"), 
                Button.inline("No", b"No")
            ],
        ]
    
    # @staticmethod
    # def get_group_keyboard(group_state: "GroupState", current_page: int = 0) -> Any:
    #     """
    #     Generate a paginated group coffee ordering keyboard.
        
    #     Creates an inline keyboard for coffee ordering with:
    #     - Member names with +/- buttons for coffee counts
    #     - Pagination controls for large groups (>15 members)
    #     - Submit button (when orders > 0) and Cancel button
        
    #     Args:
    #         group_state: Current state of group coffee ordering
    #         current_page: Current page for pagination (overrides group_state.current_page if provided)
            
    #     Returns:
    #         List of button rows for the inline keyboard
    #     """
    #     keyboard_group = []
    #     total = group_state.get_total_coffees()
        
    #     items = list(group_state.members.items())
    #     pages = len(items) // 15
        
    #     i_start = current_page * 15
    #     i_end = ((current_page + 1) * 15) if (current_page < pages) else None
        
    #     for name, value in items[i_start : i_end]:
    #         keyboard_group.append([
    #             Button.inline(str(name), "group_name"),
    #             Button.inline(str(value), "group_value"),
    #             Button.inline("+", f"group_plus_{name}"),
    #             Button.inline("-", f"group_minus_{name}")
    #         ])
            
    #     if pages > 0:
    #         navigation_buttons = []
    #         if current_page > 0:
    #             navigation_buttons.append(
    #                 Button.inline("prev", "group_prev")
    #             )
                
    #         if current_page < pages:
    #             navigation_buttons.append(
    #                 Button.inline("next", "group_next")
    #             )
    
    #         if navigation_buttons:
    #             keyboard_group.append(navigation_buttons)
            
    #     keyboard_group.append([
    #         Button.inline("Cancel", "group_cancel")
    #     ])
        
    #     if total > 0:
    #         keyboard_group[-1].append(Button.inline(f"Submit ({total})", "group_submit"))
        
    #     return keyboard_group
    
    @staticmethod
    def get_pagination_keyboard(current_page: int, total_pages: int) -> Any:
        """
        Generate pagination controls for multi-page displays.
        
        Args:
            current_page: Current page number (0-indexed)
            total_pages: Total number of pages available
            
        Returns:
            List of button rows containing pagination controls
        """
        if total_pages <= 1:
            return []
            
        navigation_buttons = []
        
        if current_page > 0:
            navigation_buttons.append(Button.inline("◀ Previous", "page_prev"))
            
        # Add page indicator
        navigation_buttons.append(
            Button.inline(f"{current_page + 1}/{total_pages}", "page_info")
        )
        
        if current_page < total_pages - 1:
            navigation_buttons.append(Button.inline("Next ▶", "page_next"))
            
        return [navigation_buttons] if navigation_buttons else []
    
    @staticmethod
    def get_credit_main_keyboard() -> Any:
        """
        Generate the main credit overview keyboard with action buttons.
        
        Returns:
            List of button rows containing notify and mark as paid buttons
        """
        return [
            [Button.inline("📢 Notify Users", "credit_notify")],
            [Button.inline("✅ Mark as Paid", "credit_mark_paid")],
            [Button.inline("❌ Close", "credit_close")]
        ]
    
    @staticmethod
    def get_credit_debtors_keyboard(debtors: List[str]) -> Any:
        """
        Generate keyboard with buttons for each debtor who owes money.
        
        Args:
            debtors: List of debtor display names
            
        Returns:
            List of button rows, 2 debtors per row
        """
        buttons = []
        for i in range(0, len(debtors), 2):
            row = []
            for j in range(2):
                if i + j < len(debtors):
                    debtor = debtors[i + j]
                    row.append(Button.inline(f"👤 {debtor}", f"credit_debtor:{debtor}"))
            buttons.append(row)
        buttons.append([Button.inline("« Back", "credit_back_main")])
        return buttons
    
    @staticmethod
    def get_credit_debtor_debts_keyboard(debtor_name: str, debts: List[tuple]) -> Any:
        """
        Generate keyboard with buttons for each debt from a specific debtor.
        
        Args:
            debtor_name: Name of the debtor
            debts: List of tuples (card_name, outstanding_amount, debt_id)
            
        Returns:
            List of button rows with debt buttons and actions
        """
        buttons = []
        for card_name, amount, debt_id in debts:
            buttons.append([
                Button.inline(
                    f"💳 {card_name}: €{amount:.2f}", 
                    f"credit_settle:{debt_id}"
                )
            ])
        buttons.append([Button.inline("💵 Specify Custom Amount", f"credit_custom:{debtor_name}")])
        buttons.append([Button.inline("« Back", "credit_back_debtors")])
        return buttons
    
    @staticmethod
    def get_keyboard_callback_filter(user_id: int) -> events.CallbackQuery:
        """
        Create a callback query event filter for a specific user.
        
        This filter ensures that only callback queries from the specified
        user are processed, providing security and preventing interference
        from other users.
        
        Args:
            user_id: Telegram user ID to filter callbacks for
            
        Returns:
            CallbackQuery event filter configured for the specific user
        """
        return events.CallbackQuery(func=lambda e: e.sender_id == user_id)
