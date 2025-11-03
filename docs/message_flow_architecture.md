# Message Flow System – Architecture & Usage

## Overview

The Message Flow system provides a declarative way to build rich Telegram conversations using small, composable states. You define screens (states) and how they connect; the engine takes care of editing vs sending, waiting for inputs, and navigation.

## Key Benefits

- Edit instead of send: keep chats tidy by editing the prior message when possible
- Mixed interaction: combine text input and buttons in one state
- Auto-delete notifications: transient feedback that cleans itself up
- Pagination: built-in helpers for long lists with next/prev
- Validators: Numeric/Text/Regex/Custom input validation
- State lifecycle: on_enter/on_exit, back/exit handling, history
- Less boilerplate: factory helpers and utilities to build flows faster

## Core Concepts

### 1) MessageDefinition
A single screen/state in your flow. It includes:
- Content: text or text_builder
- Keyboard: static buttons or keyboard_builder
- State type: BUTTON, TEXT_INPUT, or MIXED
- Navigation: next_state_map, back_button, exit_buttons
- Text input: validator, prompt, storage key, handler
- Pagination: page size, item builders/formatters
- Notifications: styles and auto-delete
- Lifecycle: on_enter/on_exit and on_button_press

### 2) MessageFlow
Holds all states and drives the loop:
- Sends/edits messages and waits for response
- Manages history and back/exit
- Executes lifecycle hooks and notifications

### 3) ButtonCallback
Represents an inline button:
- text, callback_data
- optional callback_handler (can override navigation)

### 4) make_state factory
Preferred way to define states with fewer parameters and smart defaults. It infers state_type from what you provide (e.g., on_input_received + keyboard -> MIXED).

## Usage Pattern

### Step 1: Define Text/Keyboard Builders

```python
async def build_main_menu_text(flow_state, api, user_id) -> str:
    """Build dynamic text based on current data."""
    user = await api.repo.find_user_by_id(user_id)
    credits = await api.debt_manager.get_user_credits(user)
    
    text = f"**Main Menu**\n\n"
    text += f"You have {len(credits)} outstanding credits\n"
    return text

async def build_main_menu_keyboard(flow_state, api, user_id) -> List[List[ButtonCallback]]:
    """Build dynamic keyboard."""
    return [
        [ButtonCallback("View Credits", "view_credits")],
        [ButtonCallback("Settings", "settings")],
        [ButtonCallback("Close", "close")]
    ]
```

### Step 2: Define Button Handlers (Optional)

```python
async def handle_delete_credit(flow_state, api, user_id) -> Optional[str]:
    """Handle credit deletion, return next state."""
    credit_id = flow_state.flow_data.get('selected_credit_id')
    await api.debt_manager.delete_credit(credit_id)
    
    # Store result for confirmation message
    flow_state.flow_data['delete_result'] = "✅ Credit deleted"
    
    # Navigate to confirmation state
    return "delete_confirmation"
```

### Step 3: Create the Flow (prefer factory)

```python
from .message_flow_helpers import make_state

def create_credit_flow() -> MessageFlow:
    flow = MessageFlow()

    # Main menu
    flow.add_state(make_state(
        state_id="main",
        text_builder=build_main_menu_text,
        keyboard_builder=build_main_menu_keyboard,
        action=MessageAction.AUTO,  # edit if possible, else send
        timeout=180,
        next_state_map={
            "view_credits": "credits_list",
            "settings": "settings_main"
        },
        exit_buttons=["close"],
    ))

    # Credits list
    flow.add_state(make_state(
        state_id="credits_list",
        text_builder=build_credits_list_text,
        keyboard_builder=build_credits_list_keyboard,
        action=MessageAction.EDIT,
        timeout=120,
        back_button="back",
        next_state_map={"back": "main"},
        on_button_press=handle_credit_selection,
    ))

    return flow
```

### Step 4: Use in Conversation

```python
@managed_conversation("credit_overview", 300)
async def credit_overview_conversation(self, user_id: int, conv: Conversation, state: ConversationState) -> bool:
    flow = create_credit_flow()
    return await flow.run(conv, user_id, self.api, start_state="main")
```

## Advanced Features

### Dynamic State Navigation

```python
async def handle_button_press(data: str, flow_state, api, user_id) -> Optional[str]:
    """Override next state based on button data."""
    if data.startswith("debtor:"):
        # Extract debtor name and store in flow_data
        debtor_name = data.split(":", 1)[1]
        flow_state.flow_data['selected_debtor'] = debtor_name
        return "debtor_details"  # Navigate to debtor details
    return None  # Use default navigation
```

### Lifecycle Hooks

```python
async def on_enter_credits(flow_state, api, user_id):
    """Called when entering credits state."""
    # Prefetch data
    user = await api.repo.find_user_by_id(user_id)
    credits = await api.debt_manager.get_user_credits(user)
    flow_state.flow_data['credits'] = credits

async def on_exit_credits(flow_state, api, user_id):
    """Called when exiting credits state."""
    # Clear cached data
    flow_state.flow_data.pop('credits', None)

flow.add_state(MessageDefinition(
    state_id="credits_list",
    text_builder=build_credits_text,
    keyboard_builder=build_credits_keyboard,
    on_enter=on_enter_credits,
    on_exit=on_exit_credits
))
```

### Auto-Delete Notifications

```python
flow.add_state(MessageDefinition(
    state_id="delete_confirmation",
    text="✅ Credit deleted successfully!",
    buttons=[[ButtonCallback("◀ Back", "back")]],
    action=MessageAction.EDIT,
    notification_style=NotificationStyle.MESSAGE_TEMP,
    notification_auto_delete=3,  # Auto-delete after 3 seconds
    next_state_map={"back": "main"}
))
```

### Navigation Buttons (Easy Add/Remove)

```python
from .message_flow_helpers import NavigationButtons, GridLayout

async def build_menu_keyboard(flow_state, api, user_id):
    return [
        [ButtonCallback("Option 1", "opt1")],
        [ButtonCallback("Option 2", "opt2")],
        NavigationButtons.back()  # Single button in its own row
    ]

async def build_combined_keyboard(flow_state, api, user_id):
    return [
        [ButtonCallback("Option 1", "opt1")],
        [NavigationButtons.back(), NavigationButtons.close()]  # Multiple in same row
    ]

async def build_list_keyboard(flow_state, api, user_id):
    grid = GridLayout(items_per_row=2)
    items = [("Item 1", "i1"), ("Item 2", "i2")]
    
    # Use in footer (needs List[List[ButtonCallback]])
    return grid.build(
        items=items,
        footer_buttons=[NavigationButtons.back()]
    )

# Available (all return List[ButtonCallback]):
# NavigationButtons.back()
# NavigationButtons.close()
# NavigationButtons.cancel()
# NavigationButtons.next()
# NavigationButtons.back_and_close()    # Returns multiple buttons for same row
# NavigationButtons.back_and_next()
# NavigationButtons.cancel_and_confirm()
# NavigationButtons.save_and_cancel()

# Customize text/callbacks:
NavigationButtons.back(text="⬅️ Go Back", callback="go_back")
```

### Button-Specific Callbacks

```python
async def handle_specific_credit_action(flow_state, api, user_id) -> Optional[str]:
    """Handler for specific credit action."""
    credit_id = flow_state.flow_data.get('selected_credit')
    # Do something with the credit
    return "main"  # Return to main menu

buttons = [
    [ButtonCallback(
        "Delete Credit",
        "delete",
        callback_handler=handle_specific_credit_action  # Button-specific handler
    )],
    [ButtonCallback("Back", "back")]
]
```

### Mixed Input (text + buttons) and Validators

```python
from .message_flow import StateType, NumericValidator, TextLengthValidator, RegexValidator

async def handle_amount_input(input_text, flow_state, api, user_id) -> Optional[str]:
    # Use MoneyParser helper or your own logic
    amount = MoneyParser().parse(input_text)
    if amount is None or amount <= 0:
        return "amount_input"  # stay and re-render
    flow_state.flow_data['amount'] = amount
    return "confirm"

flow.add_state(make_state(
    state_id="amount_input",
    text= "Enter amount:",
    keyboard_builder=lambda fs, api, uid: [[ButtonCallback("◀ Back", "back")]],
    input_prompt="You can also press Back.",
    input_storage_key="amount_raw",  # avoids clobbering other data
    input_validator=NumericValidator(min_value=0),
    on_input_received=handle_amount_input,
))
```

### Pagination

```python
from .message_flow import PaginationConfig

async def fetch_items(flow_state, api, user_id):
    return await api.get_many()

def item_button(item, idx):
    return ButtonCallback(f"Item {idx+1}", f"item:{idx}")

def item_format(item, idx):
    return f"• {item.name} — {item.value}"

flow.add_state(MessageDefinition(
    state_id="items",
    text="Items:",
    pagination_config=PaginationConfig(page_size=8),
    pagination_items_builder=fetch_items,
    pagination_item_button_builder=item_button,
    pagination_item_formatter=item_format,
    next_state_map={"close": "main"},
))
```

## Migration Guide

### Before (Procedural):

```python
@managed_conversation("credit_overview", 300)
async def credit_overview_conversation(self, user_id: int, conv: Conversation, state: ConversationState) -> bool:
    message = None
    current_view = "main"
    
    while True:
        if current_view == "main":
            # Build text
            text = build_main_text()
            # Build keyboard
            keyboard = build_main_keyboard()
            # Send or edit
            if message is None:
                data, message = await self.send_keyboard_and_wait_response(...)
            else:
                data, message = await self.edit_keyboard_and_wait_response(...)
            # Handle response
            if data == "view_credits":
                current_view = "credits"
                continue
            elif data == "close":
                return True
        elif current_view == "credits":
            # ... more nested logic
```

### After (Declarative):

```python
def create_credit_flow():
    flow = MessageFlow()
    
    flow.add_state(make_state(
        state_id="main",
        text_builder=build_main_text,
        keyboard_builder=build_main_keyboard,
        next_state_map={"view_credits": "credits"},
        exit_buttons=["close"],
    ))
    
    flow.add_state(make_state(
        state_id="credits",
        text_builder=build_credits_text,
        keyboard_builder=build_credits_keyboard,
        back_button="back",
        next_state_map={"back": "main"},
    ))
    
    return flow

@managed_conversation("credit_overview", 300)
async def credit_overview_conversation(self, user_id: int, conv: Conversation, state: ConversationState) -> bool:
    flow = create_credit_flow()
    return await flow.run(conv, user_id, self.api, start_state="main")
```

## Best Practices

1. **Keep Builders Pure**: Text/keyboard builders should only read data, not modify state
2. **Use flow_data for State**: Store temporary data in `flow_state.flow_data` (use DataCache for convenience)
3. **Separate Concerns**: One state per "screen" in your UI
4. **Prefer next_state_map** over button callbacks; use `on_button_press` only when logic depends on data
5. **Validate inputs** with built-in validators; use `input_storage_key` to avoid overwriting other keys
6. **Top-level imports**: keep imports at module top (avoid local imports)

## Notes

- Confirmation dialogs are available via `MessageFlow.add_confirmation(...)`.
- Mixed text-input + buttons are supported; the engine concurrently waits for both.
- Notifications support popup and message styles; temp messages can auto-delete.
- For common patterns (lists, staging, money parsing, distribution), see `docs/message_flow_helpers_guide.md`.
