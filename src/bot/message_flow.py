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

if TYPE_CHECKING:
    from telethon.tl.custom.conversation import Conversation
    from .telethon_models import MessageModel


class MessageAction(str, Enum):
    """Action to take when displaying a message."""
    SEND = "send"  # Send a new message
    EDIT = "edit"  # Edit the existing message
    AUTO = "auto"  # Auto-detect: edit if message exists, send if not


class NotificationStyle(str, Enum):
    """Style of notification to display."""
    POPUP_BRIEF = "popup_brief"  # Brief notification at top of chat
    POPUP_ALERT = "popup_alert"  # Alert popup (user must dismiss)
    MESSAGE_TEMP = "message_temp"  # Temporary message that auto-deletes
    MESSAGE_PERM = "message_perm"  # Permanent message


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
    
    # Navigation
    next_state_map: Dict[str, str] = Field(
        default_factory=dict,
        description="Maps button callback_data to next state_id"
    )
    default_next_state: Optional[str] = Field(
        default=None,
        description="Default next state if button not in map"
    )
    exit_buttons: List[str] = Field(
        default_factory=lambda: ["close", "cancel", "done"],
        description="Button callback_data values that exit the flow"
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
        question: str,
        on_confirm_state: str,
        on_cancel_state: str,
        confirm_text: str = "✅ Yes, confirm",
        cancel_text: str = "❌ No, cancel",
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
        text = f"⚠️ **Confirmation Required**\n\n{self.question}"
        if self.warning:
            text += f"\n\n{self.warning}"
        
        return MessageDefinition(
            state_id=self.state_id,
            text=text,
            buttons=[
                [ButtonCallback(self.confirm_text, "confirm")],
                [ButtonCallback(self.cancel_text, "cancel")]
            ],
            action=self.action,
            timeout=60,
            next_state_map={
                "confirm": self.on_confirm_state,
                "cancel": self.on_cancel_state
            },
            remove_buttons_on_exit=True
        )


class MessageFlowState(BaseModel):
    """Represents the current state of a message flow."""
    current_state_id: str
    previous_state_id: Optional[str] = None
    state_history: List[str] = Field(default_factory=list)
    flow_data: Dict[str, Any] = Field(default_factory=dict)
    current_message: Optional[Any] = None  # The message object being edited
    
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
    
    def add_state(self, message_def: MessageDefinition) -> None:
        """Add a message state to the flow."""
        self.states[message_def.state_id] = message_def
    
    def get_state(self, state_id: str) -> Optional[MessageDefinition]:
        """Get a message state by ID."""
        return self.states.get(state_id)
    
    def add_confirmation(
        self,
        state_id: str,
        question: str,
        on_confirm_state: str,
        on_cancel_state: str,
        **kwargs
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
        
        # Build item buttons
        buttons = []
        for idx, item in enumerate(page_items, start=start_idx):
            if current_def.pagination_item_button_builder:
                btn = current_def.pagination_item_button_builder(item, idx)
                buttons.append([btn])
        
        # Add navigation buttons
        nav_buttons = []
        if current_page > 1:
            nav_buttons.append(ButtonCallback(config.prev_button_text, "page_prev"))
        if config.show_page_numbers and total_pages > 1:
            page_info = config.page_info_format.format(current=current_page, total=total_pages)
            nav_buttons.append(ButtonCallback(page_info, "page_info"))
        if current_page < total_pages:
            nav_buttons.append(ButtonCallback(config.next_button_text, "page_next"))
        
        if nav_buttons:
            buttons.append(nav_buttons)
        
        # Add close/back button
        buttons.append([ButtonCallback(config.close_button_text, "close")])
        
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
        
        if data == "page_next":
            pag_state["current_page"] = min(pag_state["current_page"] + 1, pag_state["total_pages"])
            return True
        elif data == "page_prev":
            pag_state["current_page"] = max(pag_state["current_page"] - 1, 1)
            return True
        elif data == "page_info":
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
            Next state_id, or None if cancelled/timeout, or "__exit__" to exit flow
        """
        # Build full prompt text
        full_text = text
        if current_def.input_prompt:
            full_text += f"\n\n{current_def.input_prompt}"
        
        # Add cancel instruction (only if not MIXED state with keyboard_builder)
        if not (current_def.state_type == StateType.MIXED and current_def.keyboard_builder):
            cancel_keywords_str = ", ".join(f"`{kw}`" for kw in current_def.input_cancel_keywords)
            full_text += f"\n\n_Type {cancel_keywords_str} to cancel._"
        
        # Build keyboard - use keyboard_builder for dynamic keyboards, or buttons for static
        cancel_keyboard = None
        if current_def.keyboard_builder:
            # Dynamic keyboard (for MIXED states)
            button_callbacks = await current_def.keyboard_builder(flow_state, api, user_id)
            cancel_keyboard = [
                [Button.inline(btn.text, btn.callback_data) for btn in row]
                for row in button_callbacks
            ]
        elif current_def.buttons:
            # Static keyboard
            cancel_keyboard = [
                [Button.inline(btn.text, btn.callback_data) for btn in row]
                for row in current_def.buttons
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
                    text_task = asyncio.create_task(conv.wait_event(events.NewMessage(incoming=True), timeout=current_def.input_timeout))
                    callback_task = asyncio.create_task(conv.wait_event(events.CallbackQuery(), timeout=current_def.input_timeout)) if cancel_keyboard else None
                    
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
                            return None
                        
                        result = done.pop().result()
                        
                        # Check if it's a callback (button press)
                        if hasattr(result, 'data'):
                            # Button callback - answer it first
                            await result.answer()
                            button_data = result.data.decode('utf-8')
                            
                            # Check if it's in exit_buttons first
                            if button_data in current_def.exit_buttons:
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
                            
                            # Otherwise check next_state_map
                            if button_data in current_def.next_state_map:
                                return current_def.next_state_map[button_data]
                            
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
                return None
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
            
            # Call on_enter hook
            if current_def.on_enter:
                await current_def.on_enter(flow_state, api, user_id)
            
            # Show on_enter notification
            if current_def.on_enter_notification:
                await self._show_notification(
                    api, user_id, current_def.on_enter_notification,
                    current_def.notification_style, current_def.notification_auto_delete
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
                    # Input cancelled or timed out
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
                button_callbacks = current_def.buttons or []
            
            # Convert ButtonCallback objects to Telethon buttons
            keyboard = [
                [Button.inline(btn.text, btn.callback_data) for btn in row]
                for row in button_callbacks
            ]
            
            # Determine action: send or edit
            action = current_def.action
            if action == MessageAction.AUTO:
                action = MessageAction.EDIT if flow_state.current_message else MessageAction.SEND
            
            # Send or edit message and wait for response
            if action == MessageAction.SEND or flow_state.current_message is None:
                data, message = await api.conversation_manager.send_keyboard_and_wait_response(
                    conv, user_id, text, keyboard, current_def.timeout
                )
                flow_state.current_message = message
            else:
                data, message = await api.conversation_manager.edit_keyboard_and_wait_response(
                    conv, user_id, text, keyboard, flow_state.current_message, current_def.timeout
                )
                flow_state.current_message = message
            
            # Handle timeout or no response
            if data is None:
                if flow_state.current_message:
                    await flow_state.current_message.delete()
                return False
            
            # Handle pagination buttons
            if current_def.pagination_config:
                if await self._handle_pagination_button(data, flow_state, flow_state.current_state_id):
                    # Pagination button clicked, stay in same state and re-render
                    continue
            
            # Check for exit buttons
            if data in current_def.exit_buttons:
                # Call on_exit hook
                if current_def.on_exit:
                    await current_def.on_exit(flow_state, api, user_id)
                
                # Show on_exit notification
                if current_def.on_exit_notification:
                    await self._show_notification(
                        api, user_id, current_def.on_exit_notification,
                        current_def.notification_style, current_def.notification_auto_delete
                    )
                
                # Remove buttons if requested
                if current_def.remove_buttons_on_exit and flow_state.current_message:
                    await api.conversation_manager.send_or_edit_message(
                        user_id, text, flow_state.current_message, remove_buttons=True
                    )
                
                return True
            
            # Check for back button
            if current_def.back_button and data == current_def.back_button:
                if flow_state.previous_state_id:
                    next_state_id = flow_state.previous_state_id
                    # Remove last state from history
                    if flow_state.state_history:
                        flow_state.state_history.pop()
                else:
                    # No previous state, treat as exit
                    return True
            else:
                # Call on_button_press hook (can override next state)
                next_state_id = None
                if current_def.on_button_press:
                    next_state_id = await current_def.on_button_press(data, flow_state, api, user_id)
                
                # If hook returned None, check button callbacks and next_state_map
                if next_state_id is None:
                    # Check button-specific callbacks
                    for row in button_callbacks:
                        for btn in row:
                            if btn.callback_data == data and btn.callback_handler:
                                next_state_id = await btn.callback_handler(flow_state, api, user_id)
                                break
                        if next_state_id:
                            break
                    
                    # Fall back to next_state_map
                    if next_state_id is None:
                        next_state_id = current_def.next_state_map.get(data)
                    
                    # If still None, check if we should stay in same state or use default
                    if next_state_id is None:
                        # If button was handled but no state returned, stay in current state
                        if data in [btn.callback_data for row in button_callbacks for btn in row]:
                            # Re-render same state
                            continue
                        # Otherwise use default_next_state
                        next_state_id = current_def.default_next_state
            
            # Validate next state exists
            if next_state_id is None or next_state_id not in self.states:
                # Invalid state, treat as exit
                return False
            
            # Call on_exit hook
            if current_def.on_exit:
                await current_def.on_exit(flow_state, api, user_id)
            
            # Update flow state
            flow_state.previous_state_id = flow_state.current_state_id
            flow_state.state_history.append(flow_state.current_state_id)
            flow_state.current_state_id = next_state_id
    
    async def _show_notification(
        self,
        api: Any,
        user_id: int,
        text: str,
        style: NotificationStyle,
        auto_delete: int
    ) -> None:
        """Show a notification based on style."""
        if style == NotificationStyle.MESSAGE_TEMP:
            await api.message_manager.send_text(
                user_id, text, vanish=False, conv=False,
                delete_after=auto_delete
            )
        elif style == NotificationStyle.MESSAGE_PERM:
            await api.message_manager.send_text(
                user_id, text, vanish=True, conv=True
            )
        # POPUP styles would need button_event, handled separately
