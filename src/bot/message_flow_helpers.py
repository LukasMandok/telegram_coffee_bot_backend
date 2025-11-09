"""
Generic helpers for MessageFlow to reduce boilerplate in conversation implementations.

These helpers provide common patterns like:
- List builders with automatic pagination and grid layouts
- Staging/commit patterns for multi-step edits
- Common input parsers and validators
"""

from typing import Any, Dict, List, Optional, Callable, Awaitable, TypeVar, Generic, TYPE_CHECKING

if TYPE_CHECKING:
    from .message_flow import MessageFlow, MessageFlowState, ButtonCallback
else:
    # Runtime imports to avoid circular dependencies
    from .message_flow import MessageFlowState, ButtonCallback

T = TypeVar('T')


class GridLayout:
    """
    Helper for building grid-based button layouts.
    
    Usage:
        grid = GridLayout(items_per_row=2)
        keyboard = grid.build(
            items=[('User 1', 'user:1'), ('User 2', 'user:2')],
            footer_buttons=[[ButtonCallback('Back', 'back')]]
        )
    """
    
    def __init__(self, items_per_row: int = 2):
        self.items_per_row = items_per_row
    
    def build(
        self,
        items: List[tuple[str, str]],  # (text, callback_data)
        header_buttons: Optional[List[List[ButtonCallback]]] = None,
        footer_buttons: Optional[List[List[ButtonCallback]]] = None
    ) -> List[List[ButtonCallback]]:
        """
        Build a grid layout with optional header/footer buttons.
        
        Args:
            items: List of (text, callback_data) tuples
            header_buttons: Buttons to show before the grid
            footer_buttons: Buttons to show after the grid
        """
        buttons = []
        
        # Add header
        if header_buttons:
            buttons.extend(header_buttons)
        
        # Build grid
        row = []
        for text, callback_data in items:
            row.append(ButtonCallback(text, callback_data))
            if len(row) == self.items_per_row:
                buttons.append(row)
                row = []
        
        # Add remaining items
        if row:
            buttons.append(row)
        
        # Add footer
        if footer_buttons:
            buttons.extend(footer_buttons)
        
        return buttons


class StagingManager(Generic[T]):
    """
    Manages a staging area for multi-step edits (like marking debts as paid).
    
    Usage:
        staging = StagingManager(flow_state, 'payments')
        staging.stage('debt_1', 10.5)
        staging.commit(lambda id, amount: api.apply_payment(id, amount))
    """
    
    def __init__(self, flow_state: MessageFlowState, staging_key: str):
        self.flow_state = flow_state
        self.staging_key = staging_key
    
    def stage(self, item_id: str, value: T):
        """Add or update a staged item."""
        if self.staging_key not in self.flow_state.flow_data:
            self.flow_state.flow_data[self.staging_key] = {}
        self.flow_state.flow_data[self.staging_key][item_id] = value
    
    def unstage(self, item_id: str):
        """Remove a staged item."""
        staged = self.flow_state.flow_data.get(self.staging_key, {})
        staged.pop(item_id, None)
    
    def clear(self):
        """Clear all staged items."""
        self.flow_state.flow_data[self.staging_key] = {}
    
    def get_staged(self) -> Dict[str, T]:
        """Get all staged items."""
        return self.flow_state.flow_data.get(self.staging_key, {})
    
    def has_changes(self) -> bool:
        """Check if there are any staged changes."""
        return bool(self.get_staged())
    
    async def commit(self, committer: Callable[[str, T], Awaitable[None]]):
        """
        Apply all staged items and clear staging area.
        
        Args:
            committer: Async function that takes (item_id, value) and applies the change
        """
        staged = self.get_staged()
        for item_id, value in staged.items():
            await committer(item_id, value)
        self.clear()


class MoneyParser:
    """
    Parse various money input formats.
    
    Usage:
        parser = MoneyParser(currency_symbol='€')
        amount = parser.parse('0,5€')  # Returns 0.5
    """
    
    def __init__(self, currency_symbol: str = '€'):
        self.currency_symbol = currency_symbol
    
    def parse(self, input_text: str) -> Optional[float]:
        """
        Parse money input, supporting formats like:
        - 0.2, .2, 0,2, ,2
        - 0.2€, .2€, 0,2€, ,2€
        - 0.2 €, .2 €, etc.
        
        Returns:
            float: Parsed amount or None if invalid
        """
        # Remove spaces and currency symbol
        cleaned = input_text.strip().replace(' ', '').replace(self.currency_symbol, '')
        
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


class ListBuilder:
    """
    Build formatted lists with optional grouping and summaries.
    
    Usage:
        builder = ListBuilder()
        text = builder.build(
            title="Users",
            items=[('John', '€10.50'), ('Jane', '€5.00')],
            summary="Total: €15.50"
        )
    """
    
    def build(
        self,
        title: Optional[str] = None,
        items: Optional[List[tuple[str, str]]] = None,  # (label, value)
        summary: Optional[str] = None,
        empty_message: str = "No items found",
        align_values: bool = False  # NEW: align currency/values to the right
    ) -> str:
        """Build a formatted list."""
        parts = []
        
        if title:
            parts.append(f"**{title}**\n")
        
        if items:
            if align_values:
                # Find the longest label to align values
                max_label_len = max(len(label) for label, _ in items)
                for label, value in items:
                    # Use monospace and right-align values
                    parts.append(f"• `{label:<{max_label_len}}  {value:>10}`")
            else:
                for label, value in items:
                    parts.append(f"• {label}: {value}")
        else:
            parts.append(empty_message)
        
        if summary:
            parts.append(f"\n**{summary}**")
        
        return "\n".join(parts)
    
    def build_grouped(
        self,
        title: Optional[str] = None,
        groups: Optional[Dict[str, List[tuple[str, str]]]] = None,  # group_name -> items
        group_summaries: Optional[Dict[str, str]] = None,  # group_name -> summary
        overall_summary: Optional[str] = None,
        empty_message: str = "No items found",
        align_values: bool = False  # NEW: align currency/values to the right
    ) -> str:
        """Build a grouped list with subtotals."""
        parts = []
        
        if title:
            parts.append(f"**{title}**\n")
        
        if groups:
            for group_name, items in groups.items():
                parts.append(f"**{group_name}**")
                
                if align_values and items:
                    # Find the longest label to align values
                    max_label_len = max(len(label) for label, _ in items)
                    for label, value in items:
                        # Use monospace and right-align values
                        parts.append(f"  • `{label:<{max_label_len}}  {value:>10}`")
                else:
                    for label, value in items:
                        parts.append(f"  • {label}: {value}")
                
                if group_summaries and group_name in group_summaries:
                    parts.append(f"  **{group_summaries[group_name]}**")
                parts.append("")  # Blank line between groups
        else:
            parts.append(empty_message)
        
        if overall_summary:
            parts.append(f"**{overall_summary}**")
        
        return "\n".join(parts)


class InputDistributor:
    """
    Distribute an amount across multiple items (e.g., paying debts oldest-first).
    
    Usage:
        distributor = InputDistributor()
        distributed = distributor.distribute(
            amount=10.0,
            items={'debt1': 5.0, 'debt2': 8.0},
            existing={'debt1': 2.0}  # Already paid 2.0 on debt1
        )
        # Returns: {'debt1': 5.0, 'debt2': 7.0}  (completes debt1, partial on debt2)
    """
    
    def distribute(
        self,
        amount: float,
        items: Dict[str, float],  # item_id -> item_amount
        existing: Optional[Dict[str, float]] = None,  # item_id -> already_paid
        sort_key: Optional[Callable[[tuple[str, float]], Any]] = None
    ) -> Dict[str, float]:
        """
        Distribute amount across items.
        
        Args:
            amount: Total amount to distribute
            items: Dictionary of item_id -> total_amount
            existing: Dictionary of item_id -> already_paid_amount
            sort_key: Optional function to sort items (default: by key)
            
        Returns:
            Dictionary of item_id -> new_total_paid_amount
        """
        existing = existing or {}
        remaining = amount
        result = {}
        
        # Sort items
        sorted_items = sorted(items.items(), key=sort_key) if sort_key else sorted(items.items())
        
        for item_id, item_amount in sorted_items:
            if remaining <= 0:
                break
            
            already_paid = existing.get(item_id, 0)
            remaining_debt = item_amount - already_paid
            
            if remaining_debt > 0:
                payment = min(remaining, remaining_debt)
                result[item_id] = already_paid + payment
                remaining -= payment
        
        return result


class SimpleStateBuilder:
    """
    Build simple states without needing separate builder functions.
    
    This is for very simple states where you just want to show text and buttons
    without complex logic.
    
    Usage:
        flow.add_state(MessageDefinition(
            state_id="confirm",
            text=SimpleStateBuilder.static("Are you sure?"),
            keyboard=SimpleStateBuilder.yes_no_keyboard()
        ))
    """
    
    @staticmethod
    def static(text: str):
        """Create a static text builder that always returns the same text."""
        async def builder(flow_state, api, user_id):
            return text
        return builder
    
    @staticmethod
    def from_flow_data(key: str, default: str = ""):
        """Create a text builder that reads from flow_data."""
        async def builder(flow_state, api, user_id):
            return flow_state.flow_data.get(key, default)
        return builder
    
    @staticmethod
    def yes_no_keyboard(
        yes_text: str = "✅ Yes",
        no_text: str = "❌ No",
        yes_callback: str = "yes",
        no_callback: str = "no"
    ):
        """Create a simple yes/no keyboard."""
        async def builder(flow_state, api, user_id):
            return [[
                ButtonCallback(yes_text, yes_callback),
                ButtonCallback(no_text, no_callback)
            ]]
        return builder
    
    @staticmethod
    def single_button_keyboard(text: str, callback: str):
        """Create a keyboard with a single button."""
        async def builder(flow_state, api, user_id):
            return [[ButtonCallback(text, callback)]]
        return builder


class NavigationButtons:
    """
    Factory for common navigation buttons that can be easily added/removed.
    
    Returns List[ButtonCallback] so you can flexibly place them in any row:
    
    Usage:
        # Single button in its own row
        buttons = [
            [ButtonCallback("Option 1", "opt1")],
            NavigationButtons.back()
        ]
        
        # Multiple buttons in same row
        buttons = [
            [ButtonCallback("Option 1", "opt1")],
            [NavigationButtons.back(), NavigationButtons.close()]
        ]
        
        # With GridLayout footer (needs List[List[ButtonCallback]])
        grid.build(items=items, footer_buttons=[NavigationButtons.back()])
    """
    
    @staticmethod
    def back(text: str = "◁ Back", callback: str = "back") -> List[ButtonCallback]:
        """Single back button."""
        return [ButtonCallback(text, callback)]
    
    @staticmethod
    def prev(text: str = "◁ Prev", callback: str = "prev") -> List[ButtonCallback]:
        """Single previous button."""
        return [ButtonCallback(text, callback)]
    
    @staticmethod
    def close(text: str = "❌ Close", callback: str = "close") -> List[ButtonCallback]:
        """Single close button."""
        return [ButtonCallback(text, callback)]
    
    @staticmethod
    def cancel(text: str = "❌ Cancel", callback: str = "cancel") -> List[ButtonCallback]:
        """Single cancel button."""
        return [ButtonCallback(text, callback)]
    
    @staticmethod
    def next(text: str = "Next ▷", callback: str = "next") -> List[ButtonCallback]:
        """Single next button."""
        return [ButtonCallback(text, callback)]
    
    @staticmethod
    def undo(text: str = "↩ Undo", callback: str = "undo") -> List[ButtonCallback]:
        """Single undo button."""
        return [ButtonCallback(text, callback)]
    
    @staticmethod
    def save(text: str = "💾 Save", callback: str = "save") -> List[ButtonCallback]:
        """Single save button."""
        return [ButtonCallback(text, callback)]
    
    @staticmethod
    def back_and_close(
        back_text: str = "◁ Back",
        close_text: str = "❌ Close",
        back_callback: str = "back",
        close_callback: str = "close"
    ) -> List[ButtonCallback]:
        """Back and close buttons (use in same row)."""
        return [
            ButtonCallback(back_text, back_callback),
            ButtonCallback(close_text, close_callback)
        ]
    
    @staticmethod
    def back_and_next(
        back_text: str = "◁ Back",
        next_text: str = "Next ▷",
        back_callback: str = "back",
        next_callback: str = "next"
    ) -> List[ButtonCallback]:
        """Back and next buttons (use in same row)."""
        return [
            ButtonCallback(back_text, back_callback),
            ButtonCallback(next_text, next_callback)
        ]

    @staticmethod
    def undo_and_save(
        undo_text: str = "↩ Undo",
        save_text: str = "💾 Save",
        undo_callback: str = "undo",
        save_callback: str = "save"
    ) -> List[ButtonCallback]:
        """Undo and Save buttons (use in same row)."""
        return [
            ButtonCallback(undo_text, undo_callback),
            ButtonCallback(save_text, save_callback)
        ]
    
    @staticmethod
    def cancel_and_confirm(
        cancel_text: str = "❌ Cancel",
        confirm_text: str = "✅ Confirm",
        cancel_callback: str = "cancel",
        confirm_callback: str = "confirm"
    ) -> List[ButtonCallback]:
        """Cancel and confirm buttons (use in same row)."""
        return [
            ButtonCallback(cancel_text, cancel_callback),
            ButtonCallback(confirm_text, confirm_callback)
        ]
    
    @staticmethod
    def save_and_cancel(
        save_text: str = "💾 Save",
        cancel_text: str = "❌ Cancel",
        save_callback: str = "save",
        cancel_callback: str = "cancel"
    ) -> List[ButtonCallback]:
        """Save and cancel buttons (use in same row)."""
        return [
            ButtonCallback(save_text, save_callback),
            ButtonCallback(cancel_text, cancel_callback)
        ]


class DynamicListFlow:
    """
    Helper for creating common "list items -> select item -> edit item" flows.
    
    This abstracts the common pattern of:
    1. Show list of items
    2. User selects item
    3. Show item details with edit options
    4. Apply changes
    
    Usage:
        flow_helper = DynamicListFlow(
            list_state_id="users_list",
            detail_state_id="user_detail",
            fetch_items=lambda api, user_id: api.get_users(),
            item_to_button=lambda user: (user.name, f"user:{user.id}"),
            fetch_detail=lambda api, item_id: api.get_user(item_id),
            detail_to_text=lambda user: f"Name: {user.name}\nEmail: {user.email}"
        )
        
        flow_helper.add_to_flow(flow)
    """
    
    def __init__(
        self,
        list_state_id: str,
        detail_state_id: str,
        fetch_items: Callable,
        item_to_button: Callable[[Any], tuple[str, str]],  # item -> (text, callback)
        fetch_detail: Optional[Callable] = None,
        detail_to_text: Optional[Callable[[Any], str]] = None,
        items_per_row: int = 2,
        list_title: str = "Select an item:",
        empty_message: str = "No items found"
    ):
        self.list_state_id = list_state_id
        self.detail_state_id = detail_state_id
        self.fetch_items = fetch_items
        self.item_to_button = item_to_button
        self.fetch_detail = fetch_detail
        self.detail_to_text = detail_to_text
        self.items_per_row = items_per_row
        self.list_title = list_title
        self.empty_message = empty_message
    
    async def _build_list_text(self, flow_state, api, user_id):
        """Build the list view text."""
        cache_key = f'{self.list_state_id}_items'
        items = await flow_state.get_or_fetch(
            cache_key,
            lambda: self.fetch_items(api, user_id)
        )
        
        if not items:
            return self.empty_message
        
        return f"**{self.list_title}**\n\n{len(items)} item(s) available"
    
    async def _build_list_keyboard(self, flow_state, api, user_id):
        """Build the list view keyboard."""
        cache_key = f'{self.list_state_id}_items'
        items = await flow_state.get_or_fetch(
            cache_key,
            lambda: self.fetch_items(api, user_id)
        )
        
        grid = GridLayout(items_per_row=self.items_per_row)
        button_items = [self.item_to_button(item) for item in items]
        
        return grid.build(
            items=button_items,
            footer_buttons=[NavigationButtons.back()]
        )
    
    async def _handle_list_selection(self, data: str, flow_state, api, user_id):
        """Handle item selection from list."""
        # Extract item_id from callback (assumes format "prefix:item_id")
        if ":" in data:
            item_id = data.split(":", 1)[1]
            flow_state.set(f'{self.detail_state_id}_selected_id', item_id)
            return self.detail_state_id
        return None
    
    async def _build_detail_text(self, flow_state, api, user_id):
        """Build the detail view text."""
        item_id = flow_state.get(f'{self.detail_state_id}_selected_id')
        
        if self.fetch_detail and self.detail_to_text:
            item = await self.fetch_detail(api, item_id, user_id)
            return self.detail_to_text(item)
        
        return f"Item: {item_id}"
    
    def add_to_flow(self, flow: 'MessageFlow', **list_state_kwargs):
        """
        Add this list flow to a MessageFlow instance.
        
        Args:
            flow: The MessageFlow to add states to
            **list_state_kwargs: Additional kwargs for the list state (e.g., timeout, action)
        """
        from .message_flow import MessageDefinition, MessageAction
        
        # Add list state
        flow.add_state(MessageDefinition(
            state_id=self.list_state_id,
            text_builder=self._build_list_text,
            keyboard_builder=self._build_list_keyboard,
            action=list_state_kwargs.get('action', MessageAction.EDIT),
            timeout=list_state_kwargs.get('timeout', 120),
            on_button_press=self._handle_list_selection,
            next_state_map={"back": list_state_kwargs.get('back_state', 'main')}
        ))
        
        # Note: Detail state should be added separately by the user
        # since it may have custom edit logic


# ----------------------------------------------------------------------------
# Universal Exit States - for clean flow termination with message editing
# ----------------------------------------------------------------------------

class ExitStateBuilder:
    """
    Factory for creating exit states that properly edit the message before closing.
    
    This solves the problem of flows that need to show a final message and then
    close cleanly without leaving old messages or buttons visible.
    
    Usage:
        # In your flow definition:
        flow.add_state(ExitStateBuilder.create(
            state_id="exit_cancelled",
            text="❌ Setup Cancelled\n\nNo changes were made."
        ))
        
        # In your button handler:
        if data == "cancel":
            return "exit_cancelled"  # Navigate to exit state
    
    How it works:
    1. User clicks button that navigates to exit state
    2. Exit state EDITS current message with final text
    3. Exit state REMOVES all buttons
    4. Exit state times out after 1 second → flow auto-closes
    
    Benefits:
    - Message is properly edited (not a new message)
    - Buttons are cleanly removed
    - Flow automatically closes
    - Reusable pattern across all flows
    """
    
    @staticmethod
    def create(
        state_id: str,
        text: Optional[str] = None,
        text_builder: Optional[Callable[..., Awaitable[str]]] = None,
        timeout: int = 1,
    ):
        """
        Create an exit state that edits the message and closes the flow.
        
        Args:
            state_id: Unique state identifier (e.g., "exit_cancelled")
            text: Static text to display (provide text OR text_builder)
            text_builder: Dynamic text builder function (provide text OR text_builder)
            timeout: Seconds before auto-closing (default: 1)
            
        Returns:
            MessageDefinition for the exit state
        """
        from .message_flow import MessageDefinition, MessageAction
        
        if (text is None) == (text_builder is None):
            raise ValueError("ExitStateBuilder.create requires exactly one of 'text' or 'text_builder'")
        
        return MessageDefinition(
            state_id=state_id,
            text=text,
            text_builder=text_builder,
            buttons=None,  # Remove buttons completely (None, not [])
            action=MessageAction.EDIT,  # Edit the current message
            timeout=timeout,  # Auto-close after timeout
            remove_buttons_on_exit=True,  # Clean up buttons
        )
    
    @staticmethod
    def create_cancelled(
        state_id: str = "exit_cancelled",
        message: str = "❌ **Cancelled**\n\nNo changes were made.",
        timeout: int = 1,
    ):
        """
        Create a standard cancellation exit state.
        
        Args:
            state_id: State identifier (default: "exit_cancelled")
            message: Cancellation message
            timeout: Seconds before auto-closing
        """
        return ExitStateBuilder.create(state_id=state_id, text=message, timeout=timeout)
    
    @staticmethod
    def create_success(
        state_id: str = "exit_success",
        message: str = "✅ **Success**\n\nOperation completed successfully.",
        timeout: int = 1,
    ):
        """
        Create a standard success exit state.
        
        Args:
            state_id: State identifier (default: "exit_success")
            message: Success message
            timeout: Seconds before auto-closing
        """
        return ExitStateBuilder.create(state_id=state_id, text=message, timeout=timeout)


# ----------------------------------------------------------------------------
# State factory to reduce boilerplate when defining MessageFlow states
# ----------------------------------------------------------------------------

from typing import Awaitable as _Awaitable, Callable as _Callable
if TYPE_CHECKING:
    from .message_flow import StateType as _StateTypeHint, MessageAction as _MessageActionHint

def make_state(
    state_id: str,
    *,
    # Content (provide exactly one of text or text_builder)
    text: Optional[str] = None,
    text_builder: Optional[_Callable[..., _Awaitable[str]]] = None,
    # Keyboard (provide at most one of buttons or keyboard_builder)
    buttons: Optional[List[List["ButtonCallback"]]] = None,
    keyboard_builder: Optional[_Callable[..., _Awaitable[List[List["ButtonCallback"]]]]] = None,
    # Interaction and behavior
    state_type: Optional["_StateTypeHint"] = None,
    action: Optional["_MessageActionHint"] = None,
    timeout: Optional[int] = None,
    # Navigation
    next_state_map: Optional[Dict[str, str]] = None,
    exit_buttons: Optional[List[str]] = None,
    back_button: Optional[str] = None,
    # Text input options
    input_prompt: Optional[str] = None,
    input_storage_key: Optional[str] = None,
    input_validator: Optional[Any] = None,
    on_input_received: Optional[_Callable[..., _Awaitable[Optional[str]]]] = None,
    # Button handler
    on_button_press: Optional[_Callable[..., _Awaitable[Optional[str]]]] = None,
):
    """Factory to build a MessageDefinition with smart defaults.

    Rules:
    - Provide exactly one of (text, text_builder)
    - Provide at most one of (buttons, keyboard_builder)
    - If on_input_received is provided and a keyboard is also provided -> MIXED
      If only on_input_received is provided -> TEXT_INPUT
      Otherwise -> BUTTON
    """
    # Runtime imports to avoid circular dependencies
    from .message_flow import MessageDefinition as _MessageDefinition
    from .message_flow import StateType as _StateType, MessageAction as _MessageAction
    from .message_flow import ButtonCallback as _ButtonCallback  # noqa: F401 (type reference)

    # Validate text
    if (text is None) == (text_builder is None):
        raise ValueError("make_state requires exactly one of 'text' or 'text_builder'")
    # Validate keyboard args
    if buttons is not None and keyboard_builder is not None:
        raise ValueError("make_state accepts only one of 'buttons' or 'keyboard_builder'")

    # Infer state_type when not explicitly provided
    inferred_state_type = state_type
    if inferred_state_type is None:
        if on_input_received is not None and (keyboard_builder is not None or buttons is not None):
            inferred_state_type = _StateType.MIXED
        elif on_input_received is not None:
            inferred_state_type = _StateType.TEXT_INPUT
        else:
            inferred_state_type = _StateType.BUTTON

    # Defaults
    use_action = action or _MessageAction.AUTO
    use_timeout = timeout or 120
    use_next_map = next_state_map or {}
    # None means use default exit buttons, empty list [] means no exit buttons
    use_exit = exit_buttons if exit_buttons is not None else ["close", "cancel", "done"]

    return _MessageDefinition(
        state_id=state_id,
        state_type=inferred_state_type,
        text=text,
        text_builder=text_builder,
        buttons=buttons,
        keyboard_builder=keyboard_builder,
        action=use_action,
        timeout=use_timeout,
        next_state_map=use_next_map,
        exit_buttons=use_exit,
        back_button=back_button,
        input_prompt=input_prompt,
        input_storage_key=input_storage_key,
        input_validator=input_validator,
        on_input_received=on_input_received,
        on_button_press=on_button_press,
    )
