import importlib
import inspect

from src.bot import settings_schema


def test_logging_settings_marked_custom():
    # Ensure logging settings that require custom handlers are flagged in schema
    log_format = settings_schema.get_setting("logging", "log_format", scope="admin")
    log_modules = settings_schema.get_setting("logging", "log_modules", scope="admin")

    assert log_format is not None, "log_format setting should exist in schema"
    assert log_format.custom_route_id == "logging_format"

    assert log_modules is not None, "log_modules setting should exist in schema"
    assert log_modules.custom_route_id == "logging_modules"


def test_custom_route_registry_registration():
    from src.bot.conversation_flows import custom_route_contracts

    custom_route_contracts.clear_registry()
    try:
        @custom_route_contracts.register_custom_route("__test_route__")
        def _dummy_factory():
            return "ok"

        registered = custom_route_contracts.get_custom_route("__test_route__")
        assert registered is _dummy_factory
        assert inspect.isfunction(registered)
    finally:
        custom_route_contracts.clear_registry()


def test_logging_modules_helpers_registered_contract():
    # Ensure importing the logging-modules helper registers the contract entry.
    from src.bot.conversation_flows import custom_route_contracts
    from src.bot.conversation_flows import logging_modules_helpers

    custom_route_contracts.clear_registry()
    importlib.reload(logging_modules_helpers)

    contract = custom_route_contracts.get_custom_route_contract("logging_modules")
    assert contract is not None
    assert contract.kind == "factory"
    assert contract.factory is logging_modules_helpers.build_logging_modules_state_helpers
    assert inspect.isfunction(contract.factory)
    assert hasattr(logging_modules_helpers, "build_logging_modules_state_helpers")
    assert inspect.isfunction(logging_modules_helpers.build_logging_modules_state_helpers)


def test_inline_custom_route_contracts_registered_by_settings_flow():
    from src.bot.conversation_flows import custom_route_contracts
    from src.bot.conversation_flows import logging_modules_helpers
    from src.bot.conversation_flows import settings_flow

    custom_route_contracts.clear_registry()
    importlib.reload(logging_modules_helpers)
    importlib.reload(settings_flow)

    logging_format_contract = custom_route_contracts.get_custom_route_contract("logging_format")
    registration_password_contract = custom_route_contracts.get_custom_route_contract("registration_password")

    assert logging_format_contract is not None
    assert logging_format_contract.kind == "inline"
    assert logging_format_contract.owner == settings_flow.__name__

    assert registration_password_contract is not None
    assert registration_password_contract.kind == "inline"
    assert registration_password_contract.owner == settings_flow.__name__

    missing = custom_route_contracts.ensure_routes_registered(["logging_format", "logging_modules", "registration_password"])
    assert missing == []
