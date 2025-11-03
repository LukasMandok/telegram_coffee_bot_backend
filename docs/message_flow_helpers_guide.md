# MessageFlow Helpers - Quick Start Guide

## Installation

The helpers are already available in your project:
```python
from .message_flow_helpers import (
    DataCache, GridLayout, StagingManager, MoneyParser,
    ListBuilder, InputDistributor, SimpleStateBuilder,
    NavigationButtons, cached_builder, invalidate_cache
)
```

---

## Common Patterns

### Pattern 0: Navigation Buttons (NEW!)

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

# Available helpers (all return List[ButtonCallback]):
# - NavigationButtons.back()           # Single back button
# - NavigationButtons.close()          # Single close button  
# - NavigationButtons.cancel()         # Single cancel button
# - NavigationButtons.next()           # Single next button
# - NavigationButtons.back_and_close() # Back + Close together
# - NavigationButtons.back_and_next()  # Back + Next together
# - NavigationButtons.cancel_and_confirm()  # Cancel + Confirm
# - NavigationButtons.save_and_cancel()     # Save + Cancel

# All methods support custom text and callbacks:
NavigationButtons.back(text="⬅️ Go Back", callback="go_back")
```

---

### Pattern 1: List of Items with Selection

```python
from .message_flow import MessageFlow, MessageDefinition, ButtonCallback, MessageAction
from .message_flow_helpers import DataCache, GridLayout

async def build_users_text(flow_state, api, user_id):
    cache = DataCache(flow_state, 'users')
    users = await cache.get_or_fetch(lambda: api.get_users())
    
    return f"**Select a user:**\n\n{len(users)} users found"

async def build_users_keyboard(flow_state, api, user_id):
    cache = DataCache(flow_state, 'users')
    users = cache.get([])
    
    grid = GridLayout(items_per_row=2)
    items = [(user.name, f"user:{user.id}") for user in users]
    
    return grid.build(
        items=items,
        footer_buttons=[NavigationButtons.back()]
    )

async def handle_user_selection(data, flow_state, api, user_id):
    if data.startswith("user:"):
        user_id = data.split(":", 1)[1]
        DataCache(flow_state, 'selected_user_id').set(user_id)
        return "user_detail"
    return None
```

---

### Pattern 2: Staging Changes with Undo/Save

```python
from .message_flow_helpers import StagingManager, DataCache

async def build_items_keyboard(flow_state, api, user_id):
    items = DataCache(flow_state, 'items').get({})
    staging = StagingManager(flow_state, 'changes')
    
    grid = GridLayout(items_per_row=2)
    
    # Build footer dynamically
    if staging.has_changes():
        footer = [
            [ButtonCallback("💾 Save", "save"), ButtonCallback("↩️ Undo", "undo")]
        ]
    else:
        footer = [NavigationButtons.back()]
    
    buttons = grid.build(
        items=[(item.name, f"toggle:{item.id}") for item in items.values()],
        footer_buttons=footer
    )
    return buttons

async def handle_items_button(data, flow_state, api, user_id):
    staging = StagingManager(flow_state, 'changes')
    
    if data.startswith("toggle:"):
        item_id = data.split(":", 1)[1]
        staging.stage(item_id, True)
        return None  # Re-render
    
    elif data == "undo":
        staging.clear()
        return None
    
    elif data == "save":
        async def apply(item_id, value):
            await api.toggle_item(item_id)
        
        await staging.commit(apply)
        return "main"
```

---

### Pattern 3: Text Input with Money Parsing

```python
from .message_flow_helpers import MoneyParser

async def handle_amount_input(input_text, flow_state, api, user_id):
    parser = MoneyParser()
    amount = parser.parse(input_text)
    
    if amount is None or amount <= 0:
        await api.message_manager.send_text(
            user_id, "❌ Invalid amount",
            vanish=True, conv=True, delete_after=2
        )
        return "amount_input"  # Stay on same state
    
    # Process amount
    DataCache(flow_state, 'payment_amount').set(amount)
    return "confirmation"
```

---

### Pattern 4: Distributing Amount Across Items

```python
from .message_flow_helpers import InputDistributor, StagingManager

async def handle_payment_input(input_text, flow_state, api, user_id):
    parser = MoneyParser()
    amount = parser.parse(input_text)
    
    if not amount:
        return "payment_input"
    
    # Get debts
    debts = DataCache(flow_state, 'debts').get({})
    staging = StagingManager(flow_state, 'payments')
    
    # Distribute amount
    distributor = InputDistributor()
    distributed = distributor.distribute(
        amount=amount,
        items=debts,  # {debt_id: amount}
        existing=staging.get_staged(),
        sort_key=lambda x: debts[x[0]].created_at  # Oldest first
    )
    
    # Stage payments
    for debt_id, total_paid in distributed.items():
        staging.stage(debt_id, total_paid)
    
    await api.message_manager.send_text(
        user_id,
        f"✅ Distributed €{amount:.2f} across {len(distributed)} debts",
        vanish=True, conv=True, delete_after=2
    )
    
    return "payment_review"
```

---

### Pattern 5: Formatted Lists with Grouping

```python
from .message_flow_helpers import ListBuilder

async def build_summary_text(flow_state, api, user_id):
    data = await api.get_grouped_data()
    
    builder = ListBuilder()
    
    # Simple list
    return builder.build(
        title="Users",
        items=[("John", "Active"), ("Jane", "Inactive")],
        summary="Total: 2 users"
    )
    
    # Or grouped list
    return builder.build_grouped(
        title="Credits by Card",
        groups={
            "Card A": [("User 1", "€5.00"), ("User 2", "€3.00")],
            "Card B": [("User 3", "€10.00")]
        },
        group_summaries={
            "Card A": "Subtotal: €8.00",
            "Card B": "Subtotal: €10.00"
        },
        overall_summary="Total: €18.00"
    )
```

---

### Pattern 6: Simple States Without Boilerplate

```python
from .message_flow_helpers import SimpleStateBuilder

flow.add_state(MessageDefinition(
    state_id="confirmation",
    text=SimpleStateBuilder.static("Are you sure you want to delete this?"),
    keyboard=SimpleStateBuilder.yes_no_keyboard(),
    action=MessageAction.EDIT,
    next_state_map={"yes": "delete_confirmed", "no": "main"}
))

flow.add_state(MessageDefinition(
    state_id="success",
    text=SimpleStateBuilder.from_flow_data('success_message', '✅ Done!'),
    keyboard=SimpleStateBuilder.single_button_keyboard("◀ Back", "back"),
    action=MessageAction.EDIT,
    next_state_map={"back": "main"}
))
```

---

### Pattern 7: Cached Builders for Expensive Operations

```python
from .message_flow_helpers import cached_builder, invalidate_cache

@cached_builder('users_list')
async def build_users_text(flow_state, api, user_id):
    # This expensive query only runs once per flow
    users = await api.get_all_users_with_stats()  # Expensive!
    
    return f"Found {len(users)} users"

# Later, when you need to refresh the cache:
async def handle_user_added(flow_state, api, user_id):
    await api.add_user(...)
    
    # Invalidate cache so next render will refresh
    invalidate_cache(flow_state, 'users_list')
    
    return "users_list"
```

---

## Complete Example: Simple CRUD Flow

```python
from .message_flow import MessageFlow, MessageDefinition, ButtonCallback, MessageAction, StateType
from .message_flow_helpers import (
    DataCache, GridLayout, SimpleStateBuilder,
    cached_builder, invalidate_cache
)

# List items
@cached_builder('items')
async def build_items_text(flow_state, api, user_id):
    items = await api.get_items()
    return f"**Your Items**\n\n{len(items)} items found"

async def build_items_keyboard(flow_state, api, user_id):
    cache = DataCache(flow_state, 'items')
    items = cache.get([])
    
    grid = GridLayout(items_per_row=2)
    return grid.build(
        items=[(item.name, f"item:{item.id}") for item in items],
        header_buttons=[[ButtonCallback("➕ Add New", "add")]],
        footer_buttons=[[ButtonCallback("❌ Close", "close")]]
    )

async def handle_items_button(data, flow_state, api, user_id):
    if data == "add":
        return "add_item"
    elif data.startswith("item:"):
        item_id = data.split(":", 1)[1]
        DataCache(flow_state, 'selected_item_id').set(item_id)
        return "item_detail"
    return None

# Add item
async def handle_add_item_input(input_text, flow_state, api, user_id):
    if len(input_text) < 3:
        await api.message_manager.send_text(
            user_id, "❌ Name too short",
            vanish=True, conv=True, delete_after=2
        )
        return "add_item"
    
    await api.create_item(input_text)
    invalidate_cache(flow_state, 'items')
    
    flow_state.flow_data['success_message'] = f"✅ Created item: {input_text}"
    return "success"

# Build flow
def create_items_flow():
    flow = MessageFlow()
    
    flow.add_state(MessageDefinition(
        state_id="main",
        text_builder=build_items_text,
        keyboard_builder=build_items_keyboard,
        action=MessageAction.AUTO,
        timeout=180,
        exit_buttons=["close"],
        on_button_press=handle_items_button
    ))
    
    flow.add_state(MessageDefinition(
        state_id="add_item",
        state_type=StateType.TEXT_INPUT,
        text=SimpleStateBuilder.static("Enter item name:"),
        action=MessageAction.EDIT,
        timeout=60,
        on_input_received=handle_add_item_input
    ))
    
    flow.add_state(MessageDefinition(
        state_id="success",
        text=SimpleStateBuilder.from_flow_data('success_message', '✅ Done!'),
        keyboard=SimpleStateBuilder.single_button_keyboard("◀ Back to List", "back"),
        action=MessageAction.EDIT,
        timeout=30,
        next_state_map={"back": "main"}
    ))
    
    return flow
```

---

## Tips & Tricks

### Tip 1: Chain Helpers

```python
# Combine DataCache + GridLayout + ListBuilder
cache = DataCache(flow_state, 'users')
users = await cache.get_or_fetch(lambda: api.get_users())

builder = ListBuilder()
text = builder.build(title="Users", items=[(u.name, u.status) for u in users])

grid = GridLayout(items_per_row=2)
keyboard = grid.build(items=[(u.name, f"user:{u.id}") for u in users])
```

### Tip 2: Custom Sort in GridLayout

```python
# Sort items before building grid
items = sorted(users, key=lambda u: u.name)
grid_items = [(u.name, f"user:{u.id}") for u in items]

grid = GridLayout(items_per_row=2)
keyboard = grid.build(items=grid_items)
```

### Tip 3: Conditional Buttons

```python
staging = StagingManager(flow_state, 'changes')

footer = (
    [[ButtonCallback("💾 Save", "save"), ButtonCallback("↩️ Undo", "undo")]] 
    if staging.has_changes() 
    else [[ButtonCallback("◀ Back", "back")]]
)

grid = GridLayout(items_per_row=2)
keyboard = grid.build(items=items, footer_buttons=footer)
```

### Tip 4: Share Cache Across States

```python
# State 1: Fetch and cache
@cached_builder('shared_data')
async def build_state1_text(flow_state, api, user_id):
    data = await api.expensive_query()
    return format_data(data)

# State 2: Reuse cache
async def build_state2_text(flow_state, api, user_id):
    cache = DataCache(flow_state, 'shared_data')
    data = cache.get()  # No query needed!
    return format_differently(data)
```

---

## Summary

**Use helpers to:**
- ✅ Reduce boilerplate by 40-60%
- ✅ Make code self-documenting
- ✅ Avoid bugs from copy-paste
- ✅ Build flows faster

**Most common helpers:**
1. `DataCache` - Every flow that fetches data
2. `GridLayout` - Every list of buttons
3. `StagingManager` - Any multi-step edit
4. `MoneyParser` / `SimpleStateBuilder` - As needed
