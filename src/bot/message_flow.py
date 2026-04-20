"""
Message Flow System for Telegram Bot

This module provides a declarative way to define message flows with:
- Message/keyboard editing instead of sending new messages
- Auto-deleting notifications
- Callback functions linked to inline buttons
- Chainable message states with edit/send logic
"""

from typing import Any, Optional, List, Dict, Callable, Awaitable, Union, TYPE_CHECKING, Sequence, TypeVar
import asyncio
from telethon import Button, events
from pydantic import BaseModel, Field
from dataclasses import dataclass
from enum import Enum
from ..common.log import Logger
from .message_manager import NotificationStyle
from .message_flow_ids import CommonCallbacks


_LEGACY_EXIT_SENTINEL = "__exit__"


class _FlowExit(Exception):
    """Internal control-flow signal to exit a flow without using a string sentinel."""

    def __init__(
        self,
        *,
        completed: bool,
        rendered_text: Optional[str] = None,
        exit_callback_data: Optional[str] = None,
    ):
        super().__init__("flow_exit")
        self.completed = completed
        self.rendered_text = rendered_text
        self.exit_callback_data = exit_callback_data

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


T = TypeVar("T")


def paginate_items_0_indexed(
    items: Sequence[T],
    *,
    page: int,
    per_page: int,
) -> tuple[List[T], int, int, int, int, int]:
    """Paginate a sequence with a 0-indexed page index.

    Returns:
        (page_items, page, total_pages, start, end, total_items)
    """
    if per_page <= 0:
        raise ValueError("per_page must be > 0")

    total_items = len(items)
    total_pages = max(1, (total_items + per_page - 1) // per_page)

    if page < 0:
        page = 0
    if page >= total_pages:
        page = total_pages - 1

    start = page * per_page
    end = min(start + per_page, total_items)

    return list(items[start:end]), page, total_pages, start, end, total_items


def build_pagination_nav_row(
    *,
    current_page: int,
    total_pages: int,
    config: PaginationConfig,
    prev_callback: str,
    info_callback: str,
    next_callback: str,
    prev_handler: Optional[Callable[..., Awaitable[Optional[str]]]] = None,
    info_handler: Optional[Callable[..., Awaitable[Optional[str]]]] = None,
    next_handler: Optional[Callable[..., Awaitable[Optional[str]]]] = None,
) -> List[ButtonCallback]:
    """Build a single pagination navigation row.

    This intentionally mirrors MessageFlow's default pagination UX.

    Args:
        current_page: 1-indexed current page.
        total_pages: Total number of pages (>= 1).
        config: PaginationConfig controlling texts and formatting.
        prev_callback/info_callback/next_callback: Callback IDs.
        prev_handler/info_handler/next_handler: Optional callback handlers.

    Returns:
        A single row (List[ButtonCallback]) or [] if not needed.
    """
    if total_pages <= 1:
        return []

    row: List[ButtonCallback] = []

    if current_page > 1:
        row.append(
            ButtonCallback(
                config.prev_button_text,
                prev_callback,
                callback_handler=prev_handler,
            )
        )

    if config.show_page_numbers:
        info_text = config.page_info_format.format(current=current_page, total=total_pages)
        row.append(ButtonCallback(info_text, info_callback, callback_handler=info_handler))

    if current_page < total_pages:
        row.append(
            ButtonCallback(
                config.next_button_text,
                next_callback,
                callback_handler=next_handler,
            )
        )

    return row


def build_telethon_pagination_nav_keyboard(
    *,
    current_page: int,
    total_pages: int,
    config: PaginationConfig,
    prev_callback: str,
    info_callback: str,
    next_callback: str,
) -> List[List[Any]]:
    """Build a Telethon inline keyboard row for pagination navigation."""
    row = build_pagination_nav_row(
        current_page=current_page,
        total_pages=total_pages,
        config=config,
        prev_callback=prev_callback,
        info_callback=info_callback,
        next_callback=next_callback,
    )
    if not row:
        return []

    return [[Button.inline(btn.text, btn.callback_data) for btn in row]]


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

    pagination_extra_buttons_builder: Optional[
        Callable[["MessageFlowState", Any, int], Awaitable[List[List[ButtonCallback]]]]
    ] = Field(
        default=None,
        description=(
            "Optional dynamic buttons to prepend above paginated items/navigation. "
            "Useful for filter toggles that must reflect current state."
        ),
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

    on_timeout: Optional[Callable[..., Awaitable[Optional[str]]]] = Field(
        default=None,
        description=(
            "Async function called when waiting for user input times out. "
            "If provided, MessageFlow will NOT raise asyncio.TimeoutError; instead it will "
            "call this handler. Return a state_id to navigate (or the current state_id to re-render), "
            "or None to apply default timeout behavior. To exit intentionally, prefer navigating to an "
            "explicit terminal state (auto_exit_after_render=True)."
        ),
    )

    on_render: Optional[Callable[..., Awaitable[None]]] = Field(
        default=None,
        description=(
            "Async function called after the message for this state was rendered (sent/edited) "
            "and flow_state.current_message is available. Useful for side-effects like registering "
            "message ids for external sync systems."
        ),
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

    def add_aux_message(self, peer_id: int, message_id: int) -> None:
        """Register an auxiliary message to be deleted when the flow exits.

        This is useful for small status/info messages sent during a flow that
        should not remain in chat after the main UI is gone.
        """

        aux = self.flow_data.get("__aux_messages")
        if aux is None:
            aux = []
            self.flow_data["__aux_messages"] = aux

        aux.append({"peer_id": int(peer_id), "message_id": int(message_id)})

    async def cleanup_aux_messages(self, api: Any) -> None:
        """Delete all registered auxiliary messages (best-effort)."""

        aux = self.flow_data.pop("__aux_messages", None)
        if not aux:
            return

        per_peer: Dict[int, List[int]] = {}
        for item in aux:
            try:
                peer_id = int(item.get("peer_id"))
                message_id = int(item.get("message_id"))
            except Exception:
                continue

            if peer_id not in per_peer:
                per_peer[peer_id] = []
            per_peer[peer_id].append(message_id)

        for peer_id, message_ids in per_peer.items():
            if not message_ids:
                continue
            try:
                await api.bot.delete_messages(int(peer_id), list(message_ids))
            except Exception:
                pass
    
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
        # Optional hook for consumers that want to observe/cleanup on flow exit.
        self.on_flow_exit: Optional[Callable[..., Awaitable[None]]] = None

    @staticmethod
    def _normalize_next_state_id(value: Any) -> Optional[str]:
        """Normalize next_state_id values returned from handlers.

        Handlers sometimes accidentally return bytes or include whitespace.
        Normalizing here makes legacy/accidental return values more robust.
        """
        if value is None:
            return None

        if isinstance(value, (bytes, bytearray)):
            try:
                value = value.decode("utf-8")
            except Exception:
                value = value.decode("utf-8", errors="ignore")

        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _extract_callback_data(button: Any) -> Optional[str]:
        """Best-effort extraction of callback data from either ButtonCallback or Telethon buttons."""
        try:
            if hasattr(button, "callback_data"):
                value = getattr(button, "callback_data")
                return str(value) if value is not None else None
            if hasattr(button, "data"):
                raw = getattr(button, "data")
                if raw is None:
                    return None
                if isinstance(raw, (bytes, bytearray)):
                    return raw.decode("utf-8")
                return str(raw)
        except Exception:
            return None
        return None

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
            total_pages = max(1, (total_items + config.page_size - 1) // config.page_size)
            
            flow_state.pagination_state[state_id] = {
                "current_page": 1,
                "total_pages": total_pages,
                "items": all_items
            }
        
        pag_state = flow_state.pagination_state[state_id]
        current_page = int(pag_state.get("current_page", 1))
        total_pages = int(pag_state.get("total_pages", 1))
        all_items = pag_state["items"]

        # Defensive clamps (state may be mutated by custom handlers).
        total_pages = max(1, int(total_pages))
        current_page = max(1, min(int(current_page), total_pages))
        pag_state["current_page"] = current_page
        pag_state["total_pages"] = total_pages
        
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
        nav_row = build_pagination_nav_row(
            current_page=current_page,
            total_pages=total_pages,
            config=config,
            prev_callback=CommonCallbacks.PAGE_PREV,
            info_callback=CommonCallbacks.PAGE_INFO,
            next_callback=CommonCallbacks.PAGE_NEXT,
        )

        if nav_row:
            buttons.append(nav_row)
        
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

    async def _prepend_pagination_extras(
        self,
        flow_state: "MessageFlowState",
        current_def: MessageDefinition,
        api: Any,
        user_id: int,
        base_buttons: List[List[ButtonCallback]],
    ) -> List[List[ButtonCallback]]:
        """Prepend optional per-state extras above pagination rows."""
        extras: List[List[ButtonCallback]] = []

        if current_def.pagination_extra_buttons_builder:
            extra = await current_def.pagination_extra_buttons_builder(flow_state, api, user_id)
            if extra:
                extras.extend(extra)

        if current_def.buttons:
            extras.extend(list(current_def.buttons))

        return extras + list(base_buttons) if extras else base_buttons
    
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
            Next state_id, or None if cancelled/invalid input.
        """
        # Build base text. For TEXT_INPUT states we want paginated content (lists)
        # above the prompt, so we append `items_text` before `input_prompt`.
        full_text = text

        # Build keyboard (pagination/dynamic/static) so MIXED states can still display lists.
        cancel_keyboard = None
        button_callbacks: Optional[List[List[ButtonCallback]]] = None
        if current_def.pagination_config:
            button_callbacks, items_text = await self._build_pagination_keyboard(flow_state, current_def, api, user_id)
            button_callbacks = await self._prepend_pagination_extras(flow_state, current_def, api, user_id, button_callbacks)
            if items_text:
                full_text = f"{full_text}\n\n{items_text}"
            if current_def.input_prompt:
                full_text += f"\n\n{current_def.input_prompt}"
        elif current_def.keyboard_builder:
            # Dynamic keyboard
            button_callbacks = await current_def.keyboard_builder(flow_state, api, user_id)
        else:
            # Static keyboard (None means remove buttons)
            button_callbacks = current_def.buttons

        if not current_def.pagination_config and current_def.input_prompt:
            full_text += f"\n\n{current_def.input_prompt}"

        if button_callbacks is not None:
            # Convert ButtonCallback objects to Telethon buttons, or accept a prebuilt Telethon keyboard.
            if (
                button_callbacks
                and button_callbacks[0]
                and hasattr(button_callbacks[0][0], "callback_data")
            ):
                cancel_keyboard = [
                    [Button.inline(btn.text, btn.callback_data) for btn in row]  # type: ignore[attr-defined]
                    for row in button_callbacks
                ]
            else:
                cancel_keyboard = button_callbacks
        
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
                                raise _FlowExit(
                                    completed=True,
                                    rendered_text=full_text,
                                    exit_callback_data=button_data,
                                )
                            
                            # Check if it's the back button
                            if button_data == current_def.back_button:
                                if flow_state.previous_state_id:
                                    return flow_state.previous_state_id

                                # No previous state: treat as exit (clean up UI in caller).
                                raise _FlowExit(
                                    completed=True,
                                    rendered_text=full_text,
                                    exit_callback_data=CommonCallbacks.CANCEL,
                                )
                            
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
                                    # Only ButtonCallback supports callback_handler.
                                    if not hasattr(btn, "callback_handler"):
                                        continue
                                    if self._extract_callback_data(btn) == button_data and getattr(btn, "callback_handler"):
                                        next_state = await btn.callback_handler(flow_state, api, user_id)  # type: ignore[misc]
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
                    raise _FlowExit(
                        completed=True,
                        rendered_text=full_text,
                        exit_callback_data=CommonCallbacks.CANCEL,
                    )
                
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
            except _FlowExit:
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

        try:
            return await self._run_loop(conv, user_id, api, flow_state)
        finally:
            try:
                await flow_state.cleanup_aux_messages(api)
            except Exception:
                pass

    async def _run_loop(
        self,
        conv: "Conversation",
        user_id: int,
        api: Any,
        flow_state: MessageFlowState,
    ) -> bool:
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
                try:
                    next_state_id = await self._handle_text_input_state(
                        conv, user_id, api, flow_state, current_def, text
                    )
                except _FlowExit as e:
                    if current_def.on_exit:
                        await current_def.on_exit(flow_state, api, user_id)

                    if self.on_flow_exit is not None:
                        await self.on_flow_exit(flow_state, api, user_id)

                    if current_def.on_exit_notification:
                        await api.message_manager.send_notification(
                            user_id=user_id,
                            text=current_def.on_exit_notification,
                            style=current_def.notification_style,
                            auto_delete=current_def.notification_auto_delete,
                        )

                    # Clean up the visible UI similar to BUTTON-state exits.
                    if flow_state.current_message:
                        try:
                            if e.exit_callback_data == CommonCallbacks.CLOSE and not current_def.keep_message_on_exit:
                                await flow_state.current_message.delete()
                                flow_state.current_message = None
                            elif current_def.remove_buttons_on_exit:
                                await api.conversation_manager.send_or_edit_message(
                                    user_id,
                                    (e.rendered_text or text),
                                    flow_state.current_message,
                                    remove_buttons=True,
                                )
                        except Exception:
                            pass
                    return bool(e.completed)

                next_state_id = self._normalize_next_state_id(next_state_id)

                if next_state_id is None:
                    # Input cancelled
                    return False
                elif next_state_id == _LEGACY_EXIT_SENTINEL:
                    # Backward-compatible: allow handlers to request exit.
                    # Prefer explicit terminal states instead.
                    return True
                elif next_state_id == flow_state.current_state_id:
                    # Returning to same state - just re-render without updating history
                    continue

                if next_state_id not in self.states:
                    self.logger.warning(
                        f"Invalid next_state_id, exiting flow (current={flow_state.current_state_id}, next={next_state_id})",
                        extra_tag="FLOW",
                    )
                    return False

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
                button_callbacks = await self._prepend_pagination_extras(flow_state, current_def, api, user_id, button_callbacks)
                # Append items to text (only when provided)
                if items_text:
                    text = f"{text}\n\n{items_text}"
            elif current_def.keyboard_builder:
                button_callbacks = await current_def.keyboard_builder(flow_state, api, user_id)
            else:
                # Keep None as None (for exit states), otherwise use empty list
                button_callbacks = current_def.buttons

            # Convert ButtonCallback objects to Telethon buttons (or accept prebuilt Telethon keyboards).
            # If button_callbacks is None -> remove buttons.
            if button_callbacks is None:
                keyboard = None
            elif (
                button_callbacks
                and button_callbacks[0]
                and hasattr(button_callbacks[0][0], "callback_data")
            ):
                keyboard = [
                    [Button.inline(btn.text, btn.callback_data) for btn in row]  # type: ignore[attr-defined]
                    for row in button_callbacks
                ]
            else:
                # Already a Telethon keyboard (List[List[telethon Button]])
                keyboard = button_callbacks

            # Determine action: send or edit
            action = current_def.action
            if action == MessageAction.AUTO:
                action = MessageAction.EDIT if flow_state.current_message else MessageAction.SEND

            # Special handling for terminal/exit states.
            is_legacy_exit = keyboard is None and current_def.timeout <= 2
            is_explicit_exit = current_def.auto_exit_after_render
            if is_legacy_exit or is_explicit_exit:
                if flow_state.current_message and text.strip():
                    await api.conversation_manager.send_or_edit_message(
                        user_id, text, flow_state.current_message, remove_buttons=True
                    )
                else:
                    # Keep legacy behavior: if there is no message to edit, do not send a new one.
                    pass
                if is_legacy_exit and not is_explicit_exit:
                    await asyncio.sleep(current_def.timeout)
                return True

            # Send or edit message, then wait for response.
            try:
                if action == MessageAction.SEND or flow_state.current_message is None:
                    if keyboard is None:
                        message = await api.message_manager.send_text(user_id, text, True, True)
                    else:
                        message = await api.message_manager.send_keyboard(user_id, text, keyboard, True, True)
                    flow_state.current_message = message
                else:
                    if keyboard is None:
                        await api.message_manager.edit_message(flow_state.current_message, text, buttons=None)
                    else:
                        await api.message_manager.edit_message(flow_state.current_message, text, buttons=keyboard)
                    message = flow_state.current_message

                if current_def.on_render is not None:
                    await current_def.on_render(flow_state, api, user_id)

                if message is None:
                    return False

                data = await api.conversation_manager.receive_button_response(
                    conv, user_id, timeout=current_def.timeout
                )
            except asyncio.TimeoutError:
                if current_def.on_timeout is None:
                    raise

                next_state_id = await current_def.on_timeout(flow_state, api, user_id)
                next_state_id = self._normalize_next_state_id(next_state_id)
                if next_state_id is None:
                    # Default timeout behavior: remove the UI if possible, then exit.
                    try:
                        if flow_state.current_message:
                            await flow_state.current_message.delete()
                    except Exception:
                        pass
                    return False

                if next_state_id == _LEGACY_EXIT_SENTINEL:
                    if current_def.on_exit:
                        await current_def.on_exit(flow_state, api, user_id)

                    if current_def.on_exit_notification:
                        await api.message_manager.send_notification(
                            user_id=user_id,
                            text=current_def.on_exit_notification,
                            style=current_def.notification_style,
                            auto_delete=current_def.notification_auto_delete,
                        )

                    if flow_state.current_message and current_def.remove_buttons_on_exit:
                        try:
                            await api.conversation_manager.send_or_edit_message(
                                user_id, text, flow_state.current_message, remove_buttons=True
                            )
                        except Exception:
                            pass
                    return True

                if next_state_id == flow_state.current_state_id:
                    continue

                flow_state.previous_state_id = flow_state.current_state_id
                flow_state.state_history.append(flow_state.current_state_id)
                flow_state.current_state_id = next_state_id
                continue

            # Handle timeout or no response
            if data is None:
                if flow_state.current_message:
                    await flow_state.current_message.delete()
                return False

            # Handle pagination buttons
            if current_def.pagination_config:
                if await self._handle_pagination_button(data, flow_state, flow_state.current_state_id):
                    continue

            # Check for exit buttons
            exit_buttons_to_check = current_def.exit_buttons if current_def.exit_buttons is not None else [CommonCallbacks.CLOSE, CommonCallbacks.CANCEL, CommonCallbacks.DONE]
            if data in exit_buttons_to_check:
                if current_def.on_exit:
                    await current_def.on_exit(flow_state, api, user_id)

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
            if current_def.back_button and data == current_def.back_button:
                if flow_state.previous_state_id:
                    next_state_id = flow_state.previous_state_id
                    if flow_state.state_history:
                        flow_state.state_history.pop()
                else:
                    return True
            else:
                # Call on_button_press hook (can override next state)
                next_state_id = None
                if current_def.on_button_press:
                    next_state_id = await current_def.on_button_press(data, flow_state, api, user_id)

                if next_state_id is None:
                    # Check button-specific callbacks
                    for row in (button_callbacks or []):
                        for btn in row:
                            if not hasattr(btn, "callback_handler"):
                                continue
                            if self._extract_callback_data(btn) == data and getattr(btn, "callback_handler"):
                                next_state_id = await btn.callback_handler(flow_state, api, user_id)  # type: ignore[misc]
                                break
                        if next_state_id:
                            break

                    if next_state_id is None:
                        next_state_id = current_def.next_state_map.get(data)

                    if next_state_id is None and current_def.route_callback_to_state_id:
                        allowlist = current_def.route_callback_allowlist
                        if (allowlist is None or data in allowlist) and data in self.states:
                            next_state_id = data

                    if next_state_id is None:
                        all_button_callbacks = []
                        for row in (button_callbacks or []):
                            for btn in row:
                                cb = self._extract_callback_data(btn)
                                if cb is not None:
                                    all_button_callbacks.append(cb)
                        if data in all_button_callbacks:
                            continue
                        next_state_id = current_def.default_next_state

            next_state_id = self._normalize_next_state_id(next_state_id)

            if next_state_id == _LEGACY_EXIT_SENTINEL:
                if current_def.on_exit:
                    await current_def.on_exit(flow_state, api, user_id)

                if self.on_flow_exit is not None:
                    await self.on_flow_exit(flow_state, api, user_id)

                if current_def.on_exit_notification:
                    await api.message_manager.send_notification(
                        user_id=user_id,
                        text=current_def.on_exit_notification,
                        style=current_def.notification_style,
                        auto_delete=current_def.notification_auto_delete,
                    )

                if flow_state.current_message and current_def.remove_buttons_on_exit:
                    await api.conversation_manager.send_or_edit_message(
                        user_id, text, flow_state.current_message, remove_buttons=True
                    )
                return True

            # Validate next state exists
            if next_state_id is None or next_state_id not in self.states:
                self.logger.warning(
                    f"Invalid next_state_id, exiting flow (current={flow_state.current_state_id}, next={next_state_id})",
                    extra_tag="FLOW",
                )
                return False

            self.logger.trace(
                f"flow_transition: {flow_state.current_state_id} -> {next_state_id}",
                extra_tag="FLOW",
            )

            if current_def.on_exit:
                await current_def.on_exit(flow_state, api, user_id)

            flow_state.previous_state_id = flow_state.current_state_id
            flow_state.state_history.append(flow_state.current_state_id)
            flow_state.current_state_id = next_state_id



