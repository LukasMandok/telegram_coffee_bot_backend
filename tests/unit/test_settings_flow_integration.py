import types

import pytest

from src.bot.conversation_flows.settings_flow import create_settings_flow
from src.bot.settings_schema import ADMIN_MENU, MAIN_MENU


class FakeRepo:
    def __init__(self, is_admin: bool):
        self._is_admin = is_admin
        self._user = types.SimpleNamespace(
            group_page_size=10,
            group_sort_by="alphabetical",
            vanishing_enabled=False,
            vanishing_threshold=3,
            notifications_enabled=True,
            notifications_silent=False,
        )
        self._debt = {
            "correction_method": "absolute",
            "correction_threshold": 5,
            "creditor_exempt_from_correction": True,
            "creditor_free_coffees": 2,
        }

    async def is_user_admin(self, user_id: int) -> bool:
        return self._is_admin

    async def get_user_settings(self, user_id: int):
        return self._user

    async def get_debt_settings(self):
        return dict(self._debt)

    async def get_notification_settings(self):
        return {"notifications_enabled": True, "notifications_silent": False}

    async def get_log_settings(self):
        return {"log_level": "INFO", "log_module_overrides": {}}

    async def get_gsheet_settings(self):
        return {
            "periodic_sync_enabled": False,
            "sync_period_minutes": 30,
            "two_way_sync_enabled": False,
            "sync_after_actions_enabled": True,
        }

    async def get_snapshot_settings(self):
        return {
            "keep_last": 10,
            "weekly_full_snapshot": True,
            "card_closed": True,
            "session_completed": True,
            "quick_order": True,
            "card_created": True,
        }


class FakeMessageManager:
    def __init__(self):
        self.messages = []

    async def send_text(self, user_id, text, **kwargs):
        self.messages.append((user_id, text, kwargs))


@pytest.mark.asyncio
async def test_main_menu_routes_schema_categories_and_done():
    flow = create_settings_flow()
    handler = flow.get_state("main").on_button_press

    api = types.SimpleNamespace(
        conversation_manager=types.SimpleNamespace(repo=FakeRepo(is_admin=True)),
        message_manager=FakeMessageManager(),
    )

    for category in MAIN_MENU.categories:
        route = await handler(category, None, api, 7)
        assert route == f"sd:cat:main:{category}"

    done_route = await handler("done", None, api, 7)
    assert done_route == "settings_saved"


@pytest.mark.asyncio
async def test_main_menu_admin_routing_checks_permissions():
    flow = create_settings_flow()
    handler = flow.get_state("main").on_button_press

    non_admin_api = types.SimpleNamespace(
        conversation_manager=types.SimpleNamespace(repo=FakeRepo(is_admin=False)),
        message_manager=FakeMessageManager(),
    )
    admin_api = types.SimpleNamespace(
        conversation_manager=types.SimpleNamespace(repo=FakeRepo(is_admin=True)),
        message_manager=FakeMessageManager(),
    )

    blocked_route = await handler("admin", None, non_admin_api, 9)
    assert blocked_route == "main"
    assert len(non_admin_api.message_manager.messages) == 1

    allowed_route = await handler("admin", None, admin_api, 9)
    assert allowed_route == "admin"


@pytest.mark.asyncio
async def test_admin_menu_routes_schema_categories_and_custom_password():
    flow = create_settings_flow()
    handler = flow.get_state("admin").on_button_press

    api = types.SimpleNamespace(
        conversation_manager=types.SimpleNamespace(repo=FakeRepo(is_admin=True)),
        message_manager=FakeMessageManager(),
    )

    for category in ADMIN_MENU.categories:
        route = await handler(category, None, api, 11)
        assert route == f"sd:cat:admin:{category}"

    password_route = await handler("registration_password", None, api, 11)
    assert password_route == "registration_password"


def test_legacy_hardcoded_category_states_removed():
    flow = create_settings_flow()

    # These were legacy hardcoded states now replaced by schema-driven sd:* states.
    assert flow.get_state("ordering") is None
    assert flow.get_state("page_size") is None
    assert flow.get_state("sorting") is None
    assert flow.get_state("vanishing") is None
    assert flow.get_state("vanishing_threshold") is None
    assert flow.get_state("user_notifications") is None


@pytest.mark.asyncio
async def test_generated_category_keyboards_expose_expected_setting_callbacks():
    flow = create_settings_flow()
    api = types.SimpleNamespace(
        conversation_manager=types.SimpleNamespace(repo=FakeRepo(is_admin=True)),
        message_manager=FakeMessageManager(),
    )

    main_ordering = flow.get_state("sd:cat:main:ordering")
    assert main_ordering is not None
    main_keyboard = await main_ordering.keyboard_builder(None, api, 101)
    main_callbacks = {
        button.callback_data
        for row in main_keyboard
        for button in row
    }
    assert "sd:number_edit:ordering:group_page_size" in main_callbacks
    assert "sd:enum:ordering:group_sort_by" in main_callbacks

    admin_debts = flow.get_state("sd:cat:admin:debts")
    assert admin_debts is not None
    admin_keyboard = await admin_debts.keyboard_builder(None, api, 101)
    admin_callbacks = {
        button.callback_data
        for row in admin_keyboard
        for button in row
    }
    assert "sd:subcat:admin:debts:debt_correction" in admin_callbacks
    assert "sd:subcat:admin:debts:creditor_royalties" in admin_callbacks

    debt_correction = flow.get_state("sd:subcat:admin:debts:debt_correction")
    assert debt_correction is not None
    correction_keyboard = await debt_correction.keyboard_builder(None, api, 101)
    correction_callbacks = {
        button.callback_data
        for row in correction_keyboard
        for button in row
    }
    assert "sd:enum:debts:correction_method" in correction_callbacks
    assert "sd:number_edit:debts:correction_threshold" in correction_callbacks

    creditor_royalties = flow.get_state("sd:subcat:admin:debts:creditor_royalties")
    assert creditor_royalties is not None
    creditor_keyboard = await creditor_royalties.keyboard_builder(None, api, 101)
    creditor_callbacks = {
        button.callback_data
        for row in creditor_keyboard
        for button in row
    }
    assert "sd:toggle:debts:creditor_exempt_from_correction" in creditor_callbacks
    assert "sd:number_edit:debts:creditor_free_coffees" in creditor_callbacks
