"""
Message Flow System for Telegram Bot

This module provides a declarative way to define message flows with:
- Message/keyboard editing instead of sending new messages
- Auto-deleting notifications
- Callback functions linked to inline buttons
- Chainable message states with edit/send logic
"""

from typing import Any, Optional, List, Dict, Callable, Awaitable, Union, TYPE_CHECKING
import asyncio
from telethon import Button, events
from pydantic import BaseModel, Field
from dataclasses import dataclass
from enum import Enum
from ..common.log import Logger
from .message_manager import NotificationStyle
from .message_flow_ids import CommonCallbacks

if TYPE_CHECKING:
    from telethon.tl.custom.conversation import Conversation
    from .telethon_models import MessageModel


class MessageAction(str, Enum):
    """Action to take when displaying a message."""
    SEND = "send"  # Send a new message
    EDIT = "edit"  # Edit the existing message
    AUTO = "auto"  # Auto-detect: edit if message exists, send if not


class StateType(str, Enum):
    """Type of state interaction."""
    BUTTON = "button"  # User clicks buttons
    TEXT_INPUT = "text_input"  # User types text response
    MIXED = "mixed"  # Both buttons and text input allowed


class InputValidator:
    """Base class for input validators."""
    
    async def validate(self, text: str, flow_state: "MessageFlowState") -> tuple[bool, Optional[str]]:
        """
        Validate user input.
        
        Args:
            text: User input text
            flow_state: Current flow state
            
        Returns:
            Tuple of (is_valid, error_message)
            If valid: (True, None)
            If invalid: (False, "Error message to show user")
        """
        raise NotImplementedError


class NumericValidator(InputValidator):
    """Validates numeric input."""
    
    def __init__(self, min_value: Optional[float] = None, max_value: Optional[float] = None):
        self.min_value = min_value
        self.max_value = max_value
    
    async def validate(self, text: str, flow_state: "MessageFlowState") -> tuple[bool, Optional[str]]:
        try:
            value = float(text)
            if self.min_value is not None and value < self.min_value:
                return False, f"❌ Value must be at least {self.min_value}"
            if self.max_value is not None and value > self.max_value:
                return False, f"❌ Value must be at most {self.max_value}"
            return True, None
        except ValueError:
            return False, "❌ Please enter a valid number"


class TextLengthValidator(InputValidator):
    """Validates text length."""
    
    def __init__(self, min_length: int = 0, max_length: Optional[int] = None):
        self.min_length = min_length
        self.max_length = max_length
    
    async def validate(self, text: str, flow_state: "MessageFlowState") -> tuple[bool, Optional[str]]:
        length = len(text)
        if length < self.min_length:
            return False, f"❌ Text must be at least {self.min_length} characters"
        if self.max_length is not None and length > self.max_length:
            return False, f"❌ Text must be at most {self.max_length} characters"
        return True, None


class RegexValidator(InputValidator):
    """Validates text against a regex pattern."""
    
    def __init__(self, pattern: str, error_message: str = "❌ Invalid format"):
        import re
        self.pattern = re.compile(pattern)
        self.error_message = error_message
    
    async def validate(self, text: str, flow_state: "MessageFlowState") -> tuple[bool, Optional[str]]:
        if self.pattern.match(text):
            return True, None
        return False, self.error_message


class CustomValidator(InputValidator):
    """Validates text using a custom async function."""
    
    def __init__(self, validator_func: Callable[[str, "MessageFlowState"], Awaitable[tuple[bool, Optional[str]]]]):
        self.validator_func = validator_func
    
    async def validate(self, text: str, flow_state: "MessageFlowState") -> tuple[bool, Optional[str]]:
        return await self.validator_func(text, flow_state)


@dataclass
class ButtonCallback:
    """Represents a button with its callback data and optional handler."""
    text: str
    callback_data: str
    callback_handler: Optional[Callable[..., Awaitable[Optional[str]]]] = None  # Returns next state or None


@dataclass
class PaginationConfig:
    """Configuration for paginated lists."""
    page_size: int = 10
    items_per_row: int = 1
    show_page_numbers: bool = True
    prev_button_text: str = "◀️ Previous"
    next_button_text: str = "Next ▶️"
    close_button_text: str = "❌ Close"
    page_info_format: str = "Page {current}/{total}"  # Format string for page info


class MessageDefinition(BaseModel):
    """
    Defines a message state in a conversation flow.
    
    This is the core building block for creating menu systems.
    """
    
    # Identification
    state_id: str = Field(..., description="Unique identifier for this message state")
    
    # State Type
    state_type: StateType = Field(
        default=StateType.BUTTON,
        description="Type of state interaction"
    )
    
    # Content
    text: Optional[str] = Field(None, description="Message text (supports Markdown)")
    text_builder: Optional[Callable[..., Awaitable[str]]] = Field(
        default=None, 
        description="Dynamic text builder function"
    )
    
    # Keyboard
    buttons: Optional[List[List[ButtonCallback]]] = Field(
        default=None,
        description="2D array of buttons for inline keyboard"
    )
    keyboard_builder: Optional[Callable[..., Awaitable[List[List[ButtonCallback]]]]] = Field(
        default=None,
        description="Dynamic keyboard builder function"
    )
    
    # Text Input (for TEXT_INPUT state type)
    input_validator: Optional[InputValidator] = Field(
        default=None,
        description="Validator for text input"
    )
    input_timeout: int = Field(
        default=60,
        gt=0,
        description="Timeout for waiting for text input"
    )
    input_prompt: Optional[str] = Field(
        default=None,
        description="Additional prompt text for input (shown below main text)"
    )
    input_placeholder: Optional[str] = Field(
        default=None,
        description="Placeholder hint for input"
    )
    input_cancel_keywords: List[str] = Field(
        default_factory=lambda: ["/cancel", "cancel"],
        description="Keywords that cancel input and go back"
    )
    on_input_received: Optional[Callable[..., Awaitable[Optional[str]]]] = Field(
        default=None,
        description="Handler called when valid input is received, returns next state"
    )
    input_storage_key: Optional[str] = Field(
        default=None,
        description="Key to store input in flow_data (defaults to state_id)"
    )
    
    # Pagination (for paginated lists)
    pagination_config: Optional[PaginationConfig] = Field(
        default=None,
        description="Configuration for pagination"
    )
    pagination_reset_on_enter: bool = Field(
        default=False,
        description=(
            "If True, clears cached pagination state when this state is entered. "
            "This resets to page 1 and refetches items next render."
        )
    )
    pagination_items_builder: Optional[Callable[..., Awaitable[List[Any]]]] = Field(
        default=None,
        description="Function that returns all items to paginate"
    )
    pagination_item_formatter: Optional[Callable[[Any, int], str]] = Field(
        default=None,
        description="Function that formats a single item for display (item, index) -> str"
    )
    pagination_item_button_builder: Optional[Callable[[Any, int], ButtonCallback]] = Field(
        default=None,
        description="Function that creates a button for an item (item, index) -> ButtonCallback"
    )
    
    # Behavior
    action: MessageAction = Field(
        default=MessageAction.AUTO,
        description="How to display this message"
    )
    timeout: int = Field(
        default=120,
        gt=0,
        description="Timeout in seconds for button response"
    )
    remove_buttons_on_exit: bool = Field(
        default=True,
        description="Remove buttons when exiting this state"
    )
    keep_message_on_exit: bool = Field(
        default=False,
        description="Keep the current message when exiting this state via an exit button"
    )
    
    # Terminal behavior
    auto_exit_after_render: bool = Field(
        default=False,
        description=(
            "If True, render this state once (edit/send) and then exit the flow. "
            "This is an explicit alternative to the legacy heuristic of 'no buttons + short timeout'."
        )
    )
    
    # Navigation
    next_state_map: Dict[str, str] = Field(
        default_factory=dict,
        description="Maps button callback_data to next state_id"
    )
    default_next_state: Optional[str] = Field(
        default=None,
        description="Default next state if button not in map"
    )
    exit_buttons: Optional[List[str]] = Field(
        default=None,
        description="Button callback_data values that exit the flow. None means use default ['close', 'cancel', 'done'], empty list [] means no exit buttons."
    )
    back_button: Optional[str] = Field(
        default=None,
        description="callback_data for back button (goes to previous state)"
    )
    
    # Notifications
    on_enter_notification: Optional[str] = Field(
        default=None,
        description="Notification to show when entering this state"
    )
    on_exit_notification: Optional[str] = Field(
        default=None,
        description="Notification to show when exiting this state"
    )
    notification_style: NotificationStyle = Field(
        default=NotificationStyle.POPUP_BRIEF,
        description="Style for notifications"
    )
    notification_auto_delete: int = Field(
        default=3,
        ge=0,
        description="Auto-delete notification after N seconds (0=no auto-delete)"
    )
    
    # Lifecycle hooks
    defaults: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Default flow_data values to set on entry into this state (only if missing). "
            "Applied before on_enter."
        ),
    )
    on_enter: Optional[Callable[..., Awaitable[None]]] = Field(
        default=None,
        description="Async function called when entering this state"
    )
    on_exit: Optional[Callable[..., Awaitable[None]]] = Field(
        default=None,
        description="Async function called when exiting this state"
    )
    on_button_press: Optional[Callable[..., Awaitable[Optional[str]]]] = Field(
        default=None,
        description="Async function called on any button press, can override next state"
    )

    route_callback_to_state_id: bool = Field(
        default=False,
        description=(
            "If True and a button callback_data matches a state_id, navigate there automatically. "
            "Useful when callback_data is set to the target state_id.")
    )
    route_callback_allowlist: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional allowlist of callback_data strings that may be routed as state_ids when "
            "route_callback_to_state_id is enabled. If None, any existing state_id is allowed."
        )
    )
    
    def __init__(self, **data):
        super().__init__(**data)
        # Ensure at least text or text_builder is provided
        if self.text is None and self.text_builder is None:
            raise ValueError("Either 'text' or 'text_builder' must be provided")
        
        # Validate pagination config
        if self.pagination_config is not None:
            if self.pagination_items_builder is None:
                raise ValueError("pagination_items_builder is required when pagination_config is set")
    
    class Config:
        arbitrary_types_allowed = True


class ConfirmationDialog:
    """
    Helper for creating confirmation dialog states.
    
    Example:
        confirmation = ConfirmationDialog(
            state_id="delete_confirm",
            question="Are you sure you want to delete this credit?",
            on_confirm_state="delete_execute",
            on_cancel_state="main"
        )
        flow.add_state(confirmation.create_state())
    """
    
    def __init__(
        self,
        state_id: str,
        question: str | Callable[["MessageFlowState", Any, int], Awaitable[str]],
        on_confirm_state: str,
        on_cancel_state: str,
        confirm_text: str = "✅ Yes, confirm",
        cancel_text: str = "◁ Back",
        warning: Optional[str] = None,
        action: MessageAction = MessageAction.EDIT
    ):
        self.state_id = state_id
        self.question = question
        self.on_confirm_state = on_confirm_state
        self.on_cancel_state = on_cancel_state
        self.confirm_text = confirm_text
        self.cancel_text = cancel_text
        self.warning = warning
        self.action = action
    
    def create_state(self) -> MessageDefinition:
        """Create the confirmation dialog state."""
        async def build_text(flow_state: MessageFlowState, api: Any, user_id: int) -> str:
            question_text = await self.question(flow_state, api, user_id) if callable(self.question) else self.question
            text = f"⚠️ **Confirmation Required**\n\n{question_text}"
            if self.warning:
                text += f"\n\n{self.warning}"
            return text

        return MessageDefinition(
            state_id=self.state_id,
            text_builder=build_text,
            buttons=[
                [ButtonCallback(self.confirm_text, CommonCallbacks.CONFIRM)],
                [ButtonCallback(self.cancel_text, CommonCallbacks.BACK)],
            ],
            action=self.action,
            timeout=60,
            next_state_map={
                CommonCallbacks.CONFIRM: self.on_confirm_state,
                CommonCallbacks.BACK: self.on_cancel_state,
            },
            # Prevent 'confirm'/'back' from being treated as default exit buttons.
            exit_buttons=[],
            remove_buttons_on_exit=True,
        )


class MessageFlowState(BaseModel):
    """Represents the current state of a message flow."""
    current_state_id: str
    previous_state_id: Optional[str] = None
    state_history: List[str] = Field(default_factory=list)
    flow_data: Dict[str, Any] = Field(default_factory=dict)
    current_message: Optional[Any] = None  # The message object being edited

    # Tracks whether the current_state is being entered or re-rendered.
    last_rendered_state_id: Optional[str] = None
    
    # Pagination state
    pagination_state: Dict[str, Any] = Field(
        default_factory=dict,
        description="Stores pagination state per state_id: {state_id: {current_page: int, total_pages: int, items: List}}"
    )
    
    class Config:
        arbitrary_types_allowed = True
    
    # Helper methods for cleaner flow_data access
    def get(self, key: str, default: Any = None) -> Any:
        """Get value from flow_data with optional default."""
        return self.flow_data.get(key, default)
    
    def set(self, key: str, value: Any) -> None:
        """Set value in flow_data."""
        self.flow_data[key] = value
    
    def pop(self, key: str, default: Any = None) -> Any:
        """Remove and return value from flow_data."""
        return self.flow_data.pop(key, default)
    
    def clear(self, *keys: str) -> None:
        """
        Clear specific keys from flow_data, or all if no keys provided.
        
        Examples:
            flow_state.clear()  # Clear everything
            flow_state.clear('key1', 'key2')  # Clear specific keys
        """
        if keys:
            for key in keys:
                self.flow_data.pop(key, None)
        else:
            self.flow_data.clear()
    
    def has(self, key: str) -> bool:
        """Check if key exists in flow_data."""
        return key in self.flow_data
    
    def update(self, **kwargs) -> None:
        """Update multiple values at once."""
        self.flow_data.update(kwargs)
    
    async def get_or_fetch(self, key: str, fetch_func: Callable[[], Awaitable[Any]]) -> Any:
        """
        Get value from flow_data, or fetch and cache it if not present.
        
        Args:
            key: The key to look up in flow_data
            fetch_func: Async function to call if key doesn't exist
            
        Returns:
            The cached or freshly fetched value
            
        Example:
            items = await flow_state.get_or_fetch(
                'credits_data',
                lambda: api.get_credits(user_id)
            )
        """
        if not self.has(key):
            value = await fetch_func()
            self.set(key, value)
            return value
        return self.get(key)


class MessageFlow:
    """
    Manages a declarative message flow system.
    
    Example Usage:
    ```python
    flow = MessageFlow()
    
    # Define main menu
    flow.add_state(MessageDefinition(
        state_id="main",
        text="**Main Menu**\n\nChoose an option:",
        buttons=[
            [ButtonCallback("View Credits", "view_credits")],
            [ButtonCallback("Settings", "settings")],
            [ButtonCallback("Close", "close")]
        ],
        next_state_map={
            "view_credits": "credits_list",
            "settings": "settings_main"
        },
        exit_buttons=["close"]
    ))
    
    # Define credits list
    flow.add_state(MessageDefinition(
        state_id="credits_list",
        text_builder=lambda ctx: build_credits_text(ctx),
        keyboard_builder=lambda ctx: build_credits_keyboard(ctx),
        back_button="back",
        next_state_map={"back": "main"}
    ))
    
    # Run the flow
    await flow.run(conv, user_id, api, start_state="main")
    ```
    """
    
    def __init__(self):
        self.states: Dict[str, MessageDefinition] = {}
        self.logger = Logger("MessageFlow")

    def extend(self, other: "MessageFlow", *, overwrite: bool = False, skip_existing: bool = False) -> None:
        """Merge states from another flow.

        Args:
            other: The other MessageFlow whose states will be added.
            overwrite: If True, overwrite states with the same state_id.
            skip_existing: If True, skip states that already exist (no overwrite).
        """
        for state_id, message_def in other.states.items():
            if state_id in self.states:
                if skip_existing:
                    continue
                if not overwrite:
                    raise ValueError(f"State '{state_id}' already exists in flow")
            self.states[state_id] = message_def

    async def _wait_filtered_callback(
        self,
        conv: "Conversation",
        user_id: int,
        api: Any,
        timeout: int,
    ) -> Any:
        """Wait for a callback query using the same filtering as legacy conversations.

        This preserves existing safety behavior (scoped to the user/session) and avoids
        accidental cross-user/cross-message callback capture.
        """
        # ConversationManager.receive_button_response uses the project-specific filter.
        # We request the raw event so callers can decide whether/when to answer.
        _data, event = await api.conversation_manager.receive_button_response(
            conv, user_id, timeout=timeout, return_event=True
        )
        return event
    
    def add_state(self, message_def: MessageDefinition) -> None:
        """Add a message state to the flow."""
        self.states[message_def.state_id] = message_def
    
    def get_state(self, state_id: str) -> Optional[MessageDefinition]:
        """Get a message state by ID."""
        return self.states.get(state_id)
    
    def add_confirmation(
        self,
        state_id: str,
        question: str | Callable[["MessageFlowState", Any, int], Awaitable[str]],
        on_confirm_state: str,
        on_cancel_state: str,
        **kwargs,
    ) -> None:
        """
        Add a confirmation dialog state.
        
        Args:
            state_id: Unique ID for this confirmation
            question: Question to ask user
            on_confirm_state: State to go to on confirmation
            on_cancel_state: State to go to on cancellation
            **kwargs: Additional arguments for ConfirmationDialog
        """
        confirmation = ConfirmationDialog(
            state_id=state_id,
            question=question,
            on_confirm_state=on_confirm_state,
            on_cancel_state=on_cancel_state,
            **kwargs
        )
        self.add_state(confirmation.create_state())
    
    async def _build_pagination_keyboard(
        self,
        flow_state: MessageFlowState,
        current_def: MessageDefinition,
        api: Any,
        user_id: int
    ) -> tuple[List[List[ButtonCallback]], str]:
        """Build keyboard for paginated state with navigation buttons."""
        config = current_def.pagination_config
        if not config:
            raise ValueError("Pagination config not set")
        
        state_id = current_def.state_id
        
        # Get or initialize pagination state
        if state_id not in flow_state.pagination_state:
            # Fetch all items
            all_items = await current_def.pagination_items_builder(flow_state, api, user_id)  # type: ignore
            total_items = len(all_items)
            total_pages = (total_items + config.page_size - 1) // config.page_size
            
            flow_state.pagination_state[state_id] = {
                "current_page": 1,
                "total_pages": total_pages,
                "items": all_items
            }
        
        pag_state = flow_state.pagination_state[state_id]
        current_page = pag_state["current_page"]
        total_pages = pag_state["total_pages"]
        all_items = pag_state["items"]
        
        # Calculate page slice
        start_idx = (current_page - 1) * config.page_size
        end_idx = min(start_idx + config.page_size, len(all_items))
        page_items = all_items[start_idx:end_idx]
        
        # Build item buttons (optionally grouped into a grid)
        buttons: List[List[ButtonCallback]] = []
        items_per_row = max(1, int(config.items_per_row))
        current_row: List[ButtonCallback] = []
        for idx, item in enumerate(page_items, start=start_idx):
            if not current_def.pagination_item_button_builder:
                continue

            btn = current_def.pagination_item_button_builder(item, idx)
            current_row.append(btn)
            if len(current_row) >= items_per_row:
                buttons.append(current_row)
                current_row = []

        if current_row:
            buttons.append(current_row)
        
        # Add navigation buttons
        nav_buttons = []
        if current_page > 1:
            nav_buttons.append(ButtonCallback(config.prev_button_text, CommonCallbacks.PAGE_PREV))
        if config.show_page_numbers and total_pages > 1:
            page_info = config.page_info_format.format(current=current_page, total=total_pages)
            nav_buttons.append(ButtonCallback(page_info, CommonCallbacks.PAGE_INFO))
        if current_page < total_pages:
            nav_buttons.append(ButtonCallback(config.next_button_text, CommonCallbacks.PAGE_NEXT))
        
        if nav_buttons:
            buttons.append(nav_buttons)
        
        # Add close/back button
        buttons.append([ButtonCallback(config.close_button_text, CommonCallbacks.CLOSE)])
        
        # Build text with items
        text_parts = []
        for idx, item in enumerate(page_items, start=start_idx):
            if current_def.pagination_item_formatter:
                item_text = current_def.pagination_item_formatter(item, idx)
                text_parts.append(item_text)
        
        items_text = "\n".join(text_parts)
        
        return buttons, items_text
    
    async def _handle_pagination_button(
        self,
        data: str,
        flow_state: MessageFlowState,
        state_id: str
    ) -> bool:
        """
        Handle pagination navigation buttons.
        
        Returns:
            True if handled, False if not a pagination button
        """
        if state_id not in flow_state.pagination_state:
            return False
        
        pag_state = flow_state.pagination_state[state_id]
        
        if data == CommonCallbacks.PAGE_NEXT:
            pag_state["current_page"] = min(pag_state["current_page"] + 1, pag_state["total_pages"])
            return True
        elif data == CommonCallbacks.PAGE_PREV:
            pag_state["current_page"] = max(pag_state["current_page"] - 1, 1)
            return True
        elif data == CommonCallbacks.PAGE_INFO:
            # Page info button does nothing (just shows current page)
            return True
        
        return False
    
    async def _delete_message_after_delay(self, message: Any, delay: int):
        """Delete a message after a delay (in seconds)."""
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except Exception:
            # Message might already be deleted or not accessible
            pass
    
    async def _handle_text_input_state(
        self,
        conv: "Conversation",
        user_id: int,
        api: Any,
        flow_state: MessageFlowState,
        current_def: MessageDefinition,
        text: str
    ) -> Optional[str]:
        """
        Handle a text input state.
        
        Returns:
            Next state_id, or None if cancelled/invalid input, or "__exit__" to exit flow
        """
        # Build full prompt text
        full_text = text
        if current_def.input_prompt:
            full_text += f"\n\n{current_def.input_prompt}"
        
        # Build keyboard (pagination/dynamic/static) so MIXED states can still display lists.
        cancel_keyboard = None
        button_callbacks: Optional[List[List[ButtonCallback]]] = None
        if current_def.pagination_config:
            button_callbacks, items_text = await self._build_pagination_keyboard(flow_state, current_def, api, user_id)
            if items_text:
                full_text = f"{full_text}\n\n{items_text}"
        elif current_def.keyboard_builder:
            # Dynamic keyboard
            button_callbacks = await current_def.keyboard_builder(flow_state, api, user_id)
        else:
            # Static keyboard (None means remove buttons)
            button_callbacks = current_def.buttons

        if button_callbacks is not None:
            cancel_keyboard = [
                [Button.inline(btn.text, btn.callback_data) for btn in row]
                for row in button_callbacks
            ]
        
        # Send or edit message
        action = current_def.action
        if action == MessageAction.AUTO:
            action = MessageAction.EDIT if flow_state.current_message else MessageAction.SEND
        
        if action == MessageAction.SEND or flow_state.current_message is None:
            if cancel_keyboard:
                message = await api.message_manager.send_keyboard(
                    user_id, full_text, cancel_keyboard, True, True
                )
            else:
                message = await api.message_manager.send_text(
                    user_id, full_text, True, True
                )
            flow_state.current_message = message
        else:
            if cancel_keyboard:
                await api.message_manager.edit_message(
                    flow_state.current_message, full_text, buttons=cancel_keyboard
                )
            else:
                await api.message_manager.edit_message(
                    flow_state.current_message, full_text
                )
        
        # Wait for input (either text or button press if cancel buttons exist)
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                # Wait for either text message or button callback
                if current_def.state_type == StateType.MIXED or cancel_keyboard:
                    # Allow both text and buttons
                    
                    # Create tasks
                    # Text path uses ConversationManager to keep /cancel semantics identical.
                    text_task = asyncio.create_task(
                        api.conversation_manager.receive_message(conv, user_id, timeout=current_def.input_timeout)
                    )
                    # Callback path uses the filtered wait helper to avoid capturing unrelated callbacks.
                    callback_task = (
                        asyncio.create_task(
                            self._wait_filtered_callback(conv, user_id, api, timeout=current_def.input_timeout)
                        )
                        if cancel_keyboard
                        else None
                    )
                    
                    if callback_task:
                        done, pending = await asyncio.wait(
                            [text_task, callback_task],
                            return_when=asyncio.FIRST_COMPLETED
                        )
                        
                        # Cancel pending tasks
                        for task in pending:
                            task.cancel()
                        
                        if not done:
                            # Timeout
                            raise TimeoutError()
                        
                        result = done.pop().result()
                        
                        # Check if it's a callback (button press)
                        if hasattr(result, 'data'):
                            # Button callback - answer it first
                            await result.answer()
                            button_data = result.data.decode('utf-8')

                            # Pagination navigation (prev/next/page info)
                            if current_def.pagination_config and await self._handle_pagination_button(
                                button_data,
                                flow_state,
                                current_def.state_id,
                            ):
                                return current_def.state_id
                            
                            # Check if it's in exit_buttons first
                            # None means use default buttons, empty list means no exit buttons
                            exit_buttons_to_check = current_def.exit_buttons if current_def.exit_buttons is not None else [CommonCallbacks.CLOSE, CommonCallbacks.CANCEL, CommonCallbacks.DONE]
                            if button_data in exit_buttons_to_check:
                                return "__exit__"
                            
                            # Check if it's the back button
                            if button_data == current_def.back_button:
                                return flow_state.previous_state_id or "__exit__"
                            
                            # Call on_button_press handler
                            next_state = None
                            if current_def.on_button_press:
                                next_state = await current_def.on_button_press(button_data, flow_state, api, user_id)
                            
                            # If handler returned a state, use it
                            if next_state is not None:
                                return next_state

                            # Check button-specific callback handlers
                            for row in (button_callbacks or []):
                                for btn in row:
                                    if btn.callback_data == button_data and btn.callback_handler:
                                        next_state = await btn.callback_handler(flow_state, api, user_id)
                                        break
                                if next_state is not None:
                                    break

                            if next_state is not None:
                                return next_state
                            
                            # Otherwise check next_state_map
                            if button_data in current_def.next_state_map:
                                return current_def.next_state_map[button_data]

                            # Optional routing: callback_data == state_id
                            if current_def.route_callback_to_state_id:
                                allowlist = current_def.route_callback_allowlist
                                if (allowlist is None or button_data in allowlist) and button_data in self.states:
                                    return button_data
                            
                            # Handler returned None - stay in current state (re-render)
                            return current_def.state_id
                        else:
                            # Text message
                            message_event = result
                    else:
                        message_event = await text_task
                else:
                    # Text only
                    message_event = await api.conversation_manager.receive_message(
                        conv, user_id, current_def.input_timeout
                    )
                
                input_text = message_event.message.message.strip()
                
                # Delete the user's input message after 2 seconds (clean up conversation)
                asyncio.create_task(self._delete_message_after_delay(message_event.message, 2))
                
                # Check for cancel keywords
                if input_text.lower() in [kw.lower() for kw in current_def.input_cancel_keywords]:
                    if flow_state.previous_state_id:
                        return flow_state.previous_state_id
                    return "__exit__"
                
                # Validate input
                if current_def.input_validator:
                    is_valid, error_msg = await current_def.input_validator.validate(input_text, flow_state)
                    if not is_valid:
                        # Show error and retry
                        await api.message_manager.send_text(
                            user_id,
                            error_msg or "❌ Invalid input, please try again",
                            vanish=True,
                            conv=True,
                            delete_after=3
                        )
                        continue
                
                # Store input
                storage_key = current_def.input_storage_key or current_def.state_id
                flow_state.flow_data[storage_key] = input_text
                
                # Call on_input_received handler
                if current_def.on_input_received:
                    next_state = await current_def.on_input_received(input_text, flow_state, api, user_id)
                    if next_state is not None:
                        # Handler returned explicit state (could be current state to re-render)
                        return next_state
                
                # Use default next state (could be None, which means exit)
                return current_def.default_next_state
                
            except TimeoutError:
                raise
            except Exception as e:
                # Log error and retry
                if attempt == max_attempts - 1:
                    return None
                continue
        
        return None
    
    async def run(
        self,
        conv: "Conversation",
        user_id: int,
        api: Any,
        start_state: str,
        initial_data: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Run the message flow starting from start_state.
        
        Args:
            conv: Active Telethon conversation
            user_id: Telegram user ID
            api: TelethonAPI instance
            start_state: Starting state ID
            initial_data: Initial data for flow_data
            
        Returns:
            True if flow completed successfully, False otherwise
        """
        if start_state not in self.states:
            raise ValueError(f"Start state '{start_state}' not found in flow")
        
        # Initialize flow state
        flow_state = MessageFlowState(
            current_state_id=start_state,
            flow_data=initial_data or {}
        )
        
        while True:
            current_def = self.states[flow_state.current_state_id]

            is_state_entry = flow_state.last_rendered_state_id != flow_state.current_state_id
            flow_state.last_rendered_state_id = flow_state.current_state_id

            if is_state_entry:
                # Optional pagination reset when entering a state.
                if current_def.pagination_config and current_def.pagination_reset_on_enter:
                    flow_state.pagination_state.pop(current_def.state_id, None)

                # Apply per-state defaults (only if missing).
                if current_def.defaults:
                    for key, value in current_def.defaults.items():
                        if not flow_state.has(key):
                            flow_state.set(key, value)
            
                # Call on_enter hook (only on true entry, not on rerenders).
                if current_def.on_enter:
                    await current_def.on_enter(flow_state, api, user_id)

                # Show on_enter notification (only on true entry).
                if current_def.on_enter_notification:
                    await api.message_manager.send_notification(
                        user_id=user_id,
                        text=current_def.on_enter_notification,
                        style=current_def.notification_style,
                        auto_delete=current_def.notification_auto_delete,
                    )
            
            # Build text (dynamic or static)
            if current_def.text_builder:
                text = await current_def.text_builder(flow_state, api, user_id)
            else:
                text = current_def.text or ""
            
            # Handle different state types
            if current_def.state_type == StateType.TEXT_INPUT or current_def.state_type == StateType.MIXED:
                # Text input state or mixed (text + buttons)
                next_state_id = await self._handle_text_input_state(
                    conv, user_id, api, flow_state, current_def, text
                )
                
                if next_state_id is None:
                    # Input cancelled
                    return False
                elif next_state_id == "__exit__":
                    return True
                elif next_state_id == flow_state.current_state_id:
                    # Returning to same state - just re-render without updating history
                    continue
                
                # Update flow state and continue to new state
                flow_state.previous_state_id = flow_state.current_state_id
                flow_state.state_history.append(flow_state.current_state_id)
                flow_state.current_state_id = next_state_id
                continue
            
            # Build keyboard (dynamic, static, or paginated)
            if current_def.pagination_config:
                # Paginated keyboard
                button_callbacks, items_text = await self._build_pagination_keyboard(
                    flow_state, current_def, api, user_id
                )
                # Append items to text
                text = f"{text}\n\n{items_text}"
            elif current_def.keyboard_builder:
                button_callbacks = await current_def.keyboard_builder(flow_state, api, user_id)
            else:
                # Keep None as None (for exit states), otherwise use empty list
                button_callbacks = current_def.buttons
            
            self.logger.trace(f"State: {current_def.state_id}, buttons_value={current_def.buttons}, button_callbacks={button_callbacks}")
            self.logger.trace(f"State: {current_def.state_id}, exit_buttons={current_def.exit_buttons}")
            
            # Convert ButtonCallback objects to Telethon buttons
            # If button_callbacks is None, keep keyboard as None to remove buttons
            if button_callbacks is None:
                keyboard = None
            else:
                keyboard = [
                    [Button.inline(btn.text, btn.callback_data) for btn in row]
                    for row in button_callbacks
                ]
            
            self.logger.trace(f"State: {current_def.state_id}, keyboard={'None' if keyboard is None else f'{len(keyboard)} rows'}, timeout={current_def.timeout}")
            
            # Determine action: send or edit
            action = current_def.action
            if action == MessageAction.AUTO:
                action = MessageAction.EDIT if flow_state.current_message else MessageAction.SEND
            
            # Special handling for terminal/exit states.
            # Preserve legacy behavior: existing ExitStateBuilder used the heuristic
            # (keyboard is None and timeout <= 2). We add an explicit flag
            # `auto_exit_after_render` to support explicit terminal states without
            # relying on the heuristic. Either condition triggers the same behavior.
            is_legacy_exit = keyboard is None and current_def.timeout <= 2
            is_explicit_exit = current_def.auto_exit_after_render
            if is_legacy_exit or is_explicit_exit:
                self.logger.trace(
                    f"EXIT STATE DETECTED: state_id={current_def.state_id}, legacy={is_legacy_exit}, explicit={is_explicit_exit}, timeout={current_def.timeout}, has_message={flow_state.current_message is not None}"
                )
                if flow_state.current_message and text.strip():
                    await api.conversation_manager.send_or_edit_message(
                        user_id, text, flow_state.current_message, remove_buttons=True
                    )
                else:
                    # Keep legacy behavior: if there is no message to edit, do not send a new one.
                    pass
                # Legacy exit states historically slept for <=2s; explicit exit states
                # should return immediately to release exclusive Telethon conversations.
                if is_legacy_exit:
                    await asyncio.sleep(current_def.timeout)
                return True
            
            # Send or edit message and wait for response
            if action == MessageAction.SEND or flow_state.current_message is None:
                data, message = await api.conversation_manager.send_keyboard_and_wait_response(
                    conv, user_id, text, keyboard, current_def.timeout
                )
                flow_state.current_message = message
            else:
                data, message, _ = await api.conversation_manager.edit_keyboard_and_wait_response(
                    conv, user_id, text, keyboard, flow_state.current_message, current_def.timeout
                )
                flow_state.current_message = message
            
            self.logger.trace(f"Received button callback: data={data}")
            
            # Handle timeout or no response
            if data is None:
                self.logger.trace(f"No response received (timeout), exiting flow")
                if flow_state.current_message:
                    await flow_state.current_message.delete()
                return False
            
            # Handle pagination buttons
            if current_def.pagination_config:
                self.logger.trace(f"Checking pagination buttons")
                if await self._handle_pagination_button(data, flow_state, flow_state.current_state_id):
                    # Pagination button clicked, stay in same state and re-render
                    self.logger.trace(f"Pagination button handled, re-rendering same state")
                    continue
                self.logger.trace(f"Not a pagination button, continuing")
            
            # Check for exit buttons
            # None means use default buttons, empty list means no exit buttons
            exit_buttons_to_check = current_def.exit_buttons if current_def.exit_buttons is not None else [CommonCallbacks.CLOSE, CommonCallbacks.CANCEL, CommonCallbacks.DONE]
            self.logger.trace(f"Checking exit buttons: data={data}, exit_buttons_config={current_def.exit_buttons}, exit_buttons_to_check={exit_buttons_to_check}, data_in_exit={data in exit_buttons_to_check}")
            if data in exit_buttons_to_check:
                self.logger.trace(f"EXIT BUTTON TRIGGERED: {data}")
                # Call on_exit hook
                if current_def.on_exit:
                    await current_def.on_exit(flow_state, api, user_id)
                
                # Show on_exit notification
                if current_def.on_exit_notification:
                    await api.message_manager.send_notification(
                        user_id=user_id,
                        text=current_def.on_exit_notification,
                        style=current_def.notification_style,
                        auto_delete=current_def.notification_auto_delete,
                    )
                
                if flow_state.current_message:
                    if data == CommonCallbacks.CLOSE and not current_def.keep_message_on_exit:
                        await flow_state.current_message.delete()
                        flow_state.current_message = None
                    elif current_def.remove_buttons_on_exit:
                        await api.conversation_manager.send_or_edit_message(
                            user_id, text, flow_state.current_message, remove_buttons=True
                        )
                
                return True
            
            # Check for back button
            self.logger.trace(f"Checking back button: back_button={current_def.back_button}, data={data}, match={current_def.back_button and data == current_def.back_button}")
            if current_def.back_button and data == current_def.back_button:
                self.logger.trace(f"BACK BUTTON triggered")
                if flow_state.previous_state_id:
                    next_state_id = flow_state.previous_state_id
                    # Remove last state from history
                    if flow_state.state_history:
                        flow_state.state_history.pop()
                else:
                    # No previous state, treat as exit
                    self.logger.trace(f"No previous state, exiting")
                    return True
            else:
                # Call on_button_press hook (can override next state)
                next_state_id = None
                self.logger.trace(f"Calling on_button_press handler: has_handler={current_def.on_button_press is not None}")
                if current_def.on_button_press:
                    next_state_id = await current_def.on_button_press(data, flow_state, api, user_id)
                    self.logger.trace(f"on_button_press returned: {next_state_id}")
                
                # If hook returned None, check button callbacks and next_state_map
                if next_state_id is None:
                    self.logger.trace(f"on_button_press returned None, checking button callbacks")
                    # Check button-specific callbacks
                    for row in (button_callbacks or []):
                        for btn in row:
                            if btn.callback_data == data and btn.callback_handler:
                                self.logger.trace(f"Found button-specific callback for {data}")
                                next_state_id = await btn.callback_handler(flow_state, api, user_id)
                                self.logger.trace(f"Button callback returned: {next_state_id}")
                                break
                        if next_state_id:
                            break
                    
                    # Fall back to next_state_map
                    if next_state_id is None:
                        self.logger.trace(f"Checking next_state_map: {current_def.next_state_map}")
                        next_state_id = current_def.next_state_map.get(data)
                        self.logger.trace(f"next_state_map returned: {next_state_id}")

                    # Optional routing: callback_data == state_id
                    if next_state_id is None and current_def.route_callback_to_state_id:
                        allowlist = current_def.route_callback_allowlist
                        if (allowlist is None or data in allowlist) and data in self.states:
                            next_state_id = data
                    
                    # If still None, check if we should stay in same state or use default
                    if next_state_id is None:
                        # If button was handled but no state returned, stay in current state
                        all_button_callbacks = [btn.callback_data for row in button_callbacks for btn in row] if button_callbacks else []
                        self.logger.trace(f"next_state_id still None, checking if button exists: all_callbacks={all_button_callbacks}")
                        if data in all_button_callbacks:
                            # Re-render same state
                            self.logger.trace(f"Button exists but no next state, re-rendering current state")
                            continue
                        # Otherwise use default_next_state
                        next_state_id = current_def.default_next_state
                        self.logger.trace(f"Using default_next_state: {next_state_id}")
            
            # Validate next state exists
            self.logger.trace(f"Final next_state_id: {next_state_id}, exists_in_states={next_state_id in self.states if next_state_id else False}")
            if next_state_id is None or next_state_id not in self.states:
                # Invalid state, treat as exit
                self.logger.trace(f"next_state_id={next_state_id} not found in states, exiting")
                return False
            
            self.logger.trace(f"Navigating from {flow_state.current_state_id} to {next_state_id}")
            
            # Call on_exit hook
            self.logger.trace(f"Calling on_exit hook: has_hook={current_def.on_exit is not None}")
            if current_def.on_exit:
                await current_def.on_exit(flow_state, api, user_id)
            
            # Update flow state
            flow_state.previous_state_id = flow_state.current_state_id
            flow_state.state_history.append(flow_state.current_state_id)
            flow_state.current_state_id = next_state_id
            
            self.logger.trace(f"Successfully navigated to {next_state_id}")
    

