import asyncio
import types
import pytest

# conftest.py injects the message_flow and message_flow_helpers mocks
# automatically before tests run

from src.bot.settings_flow_generator import SettingsFlowGenerator, CATEGORIES
from src.bot.message_flow import MessageFlow, StateType


@pytest.mark.asyncio
async def test_registers_category_and_number_states():
    called_user_updates = []
    called_global_updates = []

    async def user_update_handler(flow_state, api, user_id, **kwargs):
        called_user_updates.append((user_id, kwargs))
        return True

    async def global_update_handler(flow_state, api, user_id, coro):
        # execute the coro and record
        res = await coro
        called_global_updates.append(res)
        return True

    gen = SettingsFlowGenerator(user_update_handler=user_update_handler, global_update_handler=global_update_handler)
    flow = MessageFlow()
    gen.register_schema_states(flow, parent_main="main", parent_admin="admin")

    # Basic category states
    assert flow.get_state("sd:cat:main:ordering") is not None
    assert flow.get_state("sd:cat:admin:debts") is not None

    # Number input states exist for ordering.group_page_size (user) and debts.correction_threshold (app)
    num_main = flow.get_state("sd:input:main:ordering:group_page_size")
    assert num_main is not None
    assert num_main.state_type == StateType.TEXT_INPUT

    num_admin = flow.get_state("sd:input:admin:debts:correction_threshold")
    assert num_admin is not None
    assert num_admin.state_type == StateType.TEXT_INPUT

    # on_button_press handler for category returns sd:input for number edits
    handler = flow.get_state("sd:cat:main:ordering").on_button_press
    assert handler is not None
    next_state = await handler("sd:number_edit:ordering:group_page_size", None, None, 1)
    assert next_state == "sd:input:main:ordering:group_page_size"


@pytest.mark.asyncio
async def test_enum_and_toggle_handlers_call_update_functions():
    user_calls = []
    global_calls = []

    async def user_update_handler(flow_state, api, user_id, **kwargs):
        user_calls.append((user_id, kwargs))
        return True

    async def global_update_handler(flow_state, api, user_id, coro):
        # run the updater coro and store result
        res = await coro
        global_calls.append(res)
        return True

    # Fake repo for api.conversation_manager.repo
    class FakeRepo:
        def __init__(self):
            self._debt = {"correction_method": "absolute"}
            self._user = {"vanishing_enabled": False}

        async def get_debt_settings(self):
            return dict(self._debt)

        async def update_debt_settings(self, **kwargs):
            self._debt.update(kwargs)
            return dict(self._debt)

        async def get_user_settings(self, user_id):
            return types.SimpleNamespace(**self._user)

        async def update_user_settings(self, user_id, **kwargs):
            self._user.update(kwargs)
            return types.SimpleNamespace(**self._user)

    fake_repo = FakeRepo()
    fake_api = types.SimpleNamespace(conversation_manager=types.SimpleNamespace(repo=fake_repo))

    gen = SettingsFlowGenerator(user_update_handler=user_update_handler, global_update_handler=global_update_handler)
    flow = MessageFlow()
    gen.register_schema_states(flow, parent_main="main", parent_admin="admin")

    # Enum handler for debts.correction_method (admin)
    admin_handler = flow.get_state("sd:cat:admin:debts").on_button_press
    assert admin_handler is not None
    # invoke enum button press
    res = await admin_handler("sd:enum:debts:correction_method", None, fake_api, 0)
    assert res is None
    assert len(global_calls) == 1

    # Toggle handler for vanishing (user)
    main_handler = flow.get_state("sd:cat:main:vanishing").on_button_press
    assert main_handler is not None
    res = await main_handler("sd:toggle:vanishing:vanishing_enabled", None, fake_api, 42)
    assert res is None
    assert len(user_calls) == 1


@pytest.mark.asyncio
async def test_number_input_state_validates_bounds():
    """Test that number input states enforce min/max bounds."""
    user_updates = []

    async def user_update_handler(flow_state, api, user_id, **kwargs):
        user_updates.append(kwargs)
        return True

    async def global_update_handler(flow_state, api, user_id, coro):
        return True

    gen = SettingsFlowGenerator(user_update_handler=user_update_handler, global_update_handler=global_update_handler)
    flow = MessageFlow()
    gen.register_schema_states(flow, parent_main="main", parent_admin="admin")

    # Verify numeric input state exists
    state_id = "sd:input:main:ordering:group_page_size"
    assert state_id in flow.states
    input_state = flow.states[state_id]
    assert input_state.on_input_received is not None


@pytest.mark.asyncio
async def test_number_input_rejects_out_of_bounds():
    """Test that invalid number inputs stay on current state."""
    user_updates = []
    error_messages = []

    async def user_update_handler(flow_state, api, user_id, **kwargs):
        user_updates.append(kwargs)
        return True

    async def global_update_handler(flow_state, api, user_id, coro):
        return True

    class FakeMessageManager:
        async def send_text(self, user_id, text, **kwargs):
            error_messages.append(text)

    class FakeApi:
        def __init__(self):
            self.conversation_manager = types.SimpleNamespace(repo=types.SimpleNamespace())
            self.message_manager = FakeMessageManager()

    gen = SettingsFlowGenerator(user_update_handler=user_update_handler, global_update_handler=global_update_handler)
    flow = MessageFlow()
    gen.register_schema_states(flow, parent_main="main", parent_admin="admin")

    state_id = "sd:input:main:ordering:group_page_size"
    input_state = flow.states[state_id]
    api = FakeApi()

    # Test invalid input (string)
    result = await input_state.on_input_received("invalid", None, api, 123)
    assert result == state_id  # Should stay on current state
    assert len(error_messages) > 0
    assert "Invalid input" in error_messages[0]

    # Test out-of-bounds low (group_page_size min=5)
    error_messages.clear()
    result = await input_state.on_input_received("2", None, api, 123)
    assert result == state_id
    assert len(error_messages) > 0

    # Test out-of-bounds high (group_page_size max=20)
    error_messages.clear()
    result = await input_state.on_input_received("50", None, api, 123)
    assert result == state_id
    assert len(error_messages) > 0


@pytest.mark.asyncio
async def test_number_input_accepts_valid_values():
    """Test that valid number inputs update and return to parent."""
    user_updates = []

    async def user_update_handler(flow_state, api, user_id, **kwargs):
        user_updates.append((user_id, kwargs))
        return True

    async def global_update_handler(flow_state, api, user_id, coro):
        return True

    class FakeRepo:
        async def get_user_settings(self, user_id):
            return types.SimpleNamespace(group_page_size=10)

    class FakeApi:
        def __init__(self):
            self.conversation_manager = types.SimpleNamespace(repo=FakeRepo())
            self.message_manager = types.SimpleNamespace(send_text=lambda *a, **k: None)

    gen = SettingsFlowGenerator(user_update_handler=user_update_handler, global_update_handler=global_update_handler)
    flow = MessageFlow()
    gen.register_schema_states(flow, parent_main="main", parent_admin="admin")

    state_id = "sd:input:main:ordering:group_page_size"
    parent_state = "sd:cat:main:ordering"
    input_state = flow.states[state_id]
    api = FakeApi()

    # Test valid input within bounds
    result = await input_state.on_input_received("15", None, api, 123)
    assert result == parent_state  # Should return to parent
    assert len(user_updates) == 1
    assert user_updates[0] == (123, {"group_page_size": 15})


@pytest.mark.asyncio
async def test_category_states_built_for_all_categories():
    """Test that category states are registered for all defined categories."""
    async def dummy_handler(flow_state, api, user_id, **kwargs):
        return True

    gen = SettingsFlowGenerator(user_update_handler=dummy_handler, global_update_handler=dummy_handler)
    flow = MessageFlow()
    gen.register_schema_states(flow, parent_main="main", parent_admin="admin")

    # Verify main category states (from MAIN_MENU)
    expected_main_categories = {"ordering", "vanishing", "notifications"}
    for cat_key in expected_main_categories:
        state_id = f"sd:cat:main:{cat_key}"
        assert state_id in flow.states, f"Expected category state {state_id} not found"

    # Verify admin category states (from ADMIN_MENU)
    expected_admin_categories = {"notifications", "logging", "gsheet", "snapshots", "debts"}
    for cat_key in expected_admin_categories:
        state_id = f"sd:cat:admin:{cat_key}"
        assert state_id in flow.states, f"Expected category state {state_id} not found"
