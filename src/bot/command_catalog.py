"""Command catalog shared between Telegram UI registration and command suggestions.

Keep this module free of TelethonAPI/CommandManager imports to avoid circular deps.

Schema: (command, description, show_for_normal_users)
- command: without leading '/'
- description: short Telegram UI description
- show_for_normal_users: whether to show in the default command list for non-admin users
"""

from __future__ import annotations


COMMAND_BY_CONTEXT: dict[str, str] = {
    "registration": "/start",
    "group_selection": "/order",
    "quick_order": "/order",
    "create_coffee_card": "/new_card",
    "close_card": "/close_card",
    "card_menu": "/cards",
    "debt": "/debt",
    "credit_overview": "/credit",
    "paypal_setup": "/paypal",
    "setup_paypal": "/paypal",
    "snapshots": "/snapshots",
    "users": "/users",
    "add_passive_user": "/users",
    "settings": "/settings",
    "feedback": "/feedback",
}


BOT_COMMANDS: list[tuple[str, str, bool]] = [
    # (command, description, show_for_normal_users)
    ("start", "Register with the bot", True),
    ("order", "Create or join a session to place an order", True),
    ("debt", "Show and manage your debts", True),
    ("credit", "Manage the debts others owe to you", True),
    ("cards", "Show the current status and manage all coffee cards", True),
    ("new_card", "Create a new coffee card that you paid for", False),
    ("close_card", "Close the last active coffee card", False),
    ("settings", "Adjust your personal preferences", True),
    ("feedback", "Send feedback or view your feedback", True),
    ("paypal", "Setup your paypal.me link", False),
    ("sync", "(Admin) Export current state to Google Sheets", False),
    ("snapshots", "(Admin) Create and restore snapshots", False),
    ("users", "(Admin) Manage users", False),
    ("help", "Show help and available commands", True),
]


def get_all_commands() -> list[tuple[str, str]]:
    """All commands (including admin-only)."""
    return [(cmd, desc) for cmd, desc, _ in BOT_COMMANDS]


def get_user_commands() -> list[tuple[str, str]]:
    """Commands shown to normal users in Telegram's command UI."""
    return [(cmd, desc) for cmd, desc, show_for_normal_users in BOT_COMMANDS if show_for_normal_users]


def get_admin_commands() -> list[tuple[str, str]]:
    """Admin-only commands (not shown to normal users)."""
    return [(cmd, desc) for cmd, desc, show_for_normal_users in BOT_COMMANDS if not show_for_normal_users]


def get_command_descriptions() -> dict[str, str]:
    """Map command name -> description."""
    return {cmd: desc for cmd, desc, _ in BOT_COMMANDS}
