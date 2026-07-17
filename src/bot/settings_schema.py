"""
Settings Schema - Single Source of Truth

This module defines the complete settings system structure, including:
1. All categories and menu hierarchy
2. All settings with complete metadata (labels, descriptions, constraints)
3. Validator specifications for auto-generating model validators
4. Icons and UI metadata
5. Repository method mappings

Philosophy:
- One place to define everything about settings
- Models derived from this schema
- Validators auto-generated from constraints
- No duplication between schema and models
"""

from __future__ import annotations
from enum import Enum
from typing import Any, Dict, List, Optional, Callable

from pydantic import BaseModel, Field


# ===========================
# Type Definitions
# ===========================

class SettingType(str, Enum):
    """Types of settings - determines UI interaction."""
    TOGGLE = "toggle"           # Boolean toggle
    NUMBER = "number"           # Integer with min/max
    ENUM = "enum"              # Choice from options
    TEXT = "text"              # Freeform text (for future)
    CUSTOM = "custom"          # Custom isolated handler


class ValidatorType(str, Enum):
    """Types of validators that can be auto-generated."""
    RANGE = "range"            # Min/max for numbers
    CHOICE = "choice"          # Must be in options list
    ENUM_CHOICE = "enum_choice"  # Specific allowed values
    CUSTOM = "custom"          # Custom validation function


class Validator(BaseModel):
    """Validator specification for auto-generating model validators."""
    type: ValidatorType
    value: Any = None           # For RANGE: (min, max), for CHOICE: list, etc.
    error_message: Optional[str] = None
    custom_fn: Optional[Callable] = None  # For CUSTOM type


class Setting(BaseModel):
    """Complete setting definition."""
    # Identity
    key: str
    label: str
    description: Optional[str] = None
    
    # Type and interaction
    type: SettingType
    target: str = Field(..., description="'app' or 'user'")
    section: Optional[str] = Field(None, description="App section (e.g. 'logging', 'notifications')")
    
    # Constraints (for all types)
    min_value: Optional[int] = None
    max_value: Optional[int] = None
    options: Optional[List[str]] = None
    default: Optional[Any] = None
    enum_behavior: Optional[str] = Field(
        None,
        description="How enum options are selected: 'cycle' or 'select'",
    )
    # How many columns to layout enum selection buttons in when using 'select'
    enum_columns: Optional[int] = Field(
        None,
        description="Number of columns to layout enum option buttons in the selection screen",
    )
    
    # UI/Custom
    custom_route_id: Optional[str] = None  # For CUSTOM type
    
    # Validators (auto-generated in models)
    validators: Optional[List[Validator]] = None


class SettingCategory(BaseModel):
    """A category of settings (e.g., "Logging", "Notifications")."""
    key: str
    label: str
    description: Optional[str] = None
    icon: str = "⚙️"
    settings: List[Setting] = Field(default_factory=list)
    subcategories: List[SettingSubcategory] = Field(default_factory=list)


class SettingSubcategory(BaseModel):
    """A subcategory inside a settings category."""
    key: str
    label: str
    description: Optional[str] = None
    icon: Optional[str] = None
    setting_keys: List[str] = Field(default_factory=list)
    custom_route_id: Optional[str] = None
    layout: Optional[str] = None
    button_style: Optional[str] = None


class SettingsMenu(BaseModel):
    """Complete menu hierarchy."""
    label: str
    icon: Optional[str] = None
    description: Optional[str] = None
    categories: List[str] = Field(default_factory=list)  # Category keys


# ===========================
# Icon Definitions
# ===========================

ICON_MAP: Dict[str, str] = {
    # Main menus
    "main": "⚙️",
    "admin": "🛠️",
    
    # Categories
    "ordering": "📋",
    "vanishing": "💬",
    "notifications": "🔔",
    "logging": "📊",
    "gsheet": "📄",
    "debts": "💳",
    "snapshots": "📸",
    
    # Ordering settings
    "group_page_size": "📄",
    "group_sort_by": "🔤",
    
    # Vanishing settings
    "vanishing_enabled": "💬",
    "vanishing_threshold": "🔢",
    
    # Notifications settings
    "notifications_enabled": "🔔",
    "notifications_silent": "🔕",
    
    # Logging settings
    "log_level": "📊",
    "log_show_time": "⏱",
    "log_show_caller": "👤",
    "log_show_class": "🏷",
    "log_format": "✨",
    "log_modules": "📦",
    "logging_level": "📊",
    "logging_format": "✨",
    "logging_modules": "📦",
    
    # Google Sheets settings
    "periodic_sync_enabled": "⏱",
    "sync_period_minutes": "⏱",
    "two_way_sync_enabled": "🔁",
    "sync_after_actions_enabled": "🔄",
    
    # Snapshots settings
    "keep_last": "🧾",
    "card_closed": "📌",
    "session_completed": "✅",
    "quick_order": "⚡",
    "card_created": "➕",
    "weekly_full_snapshot": "🗓",
    "snapshot_reasons": "📌",
    "snapshot_retention": "🧾",
    "snapshot_schedule": "🗓",
    
    # Debts settings
    "correction_method": "🧮",
    "correction_threshold": "🔢",
    "creditor_exempt_from_correction": "👑",
    "creditor_free_coffees": "☕",
    "debt_correction": "🧮",
    "creditor_royalties": "👑",
}


# ===========================
# Repository Method Mapping
# ===========================

SECTION_REPO_MAP: Dict[str, tuple[str, str]] = {
    "logging": ("get_log_settings", "update_log_settings"),
    "notifications": ("get_notification_settings", "update_notification_settings"),
    "debt": ("get_debt_settings", "update_debt_settings"),
    "gsheet": ("get_gsheet_settings", "update_gsheet_settings"),
    "snapshots": ("get_snapshot_settings", "update_snapshot_settings"),
}


# ===========================
# Complete Settings Schema
# ===========================

# User-Level Settings Categories

ORDERING_CATEGORY = SettingCategory(
    key="ordering",
    label="Ordering Settings",
    description="How to display and sort users in menus",
    icon=ICON_MAP["ordering"],
    settings=[
        Setting(
            key="group_page_size",
            label="Users per Page",
            description="How many users to show on each page",
            type=SettingType.NUMBER,
            target="user",
            min_value=5,
            max_value=20,
            default=10,
        ),
        Setting(
            key="group_sort_by",
            label="Sort Users By",
            description="Choose how users are ordered in group menus",
            type=SettingType.ENUM,
            target="user",
            options=["alphabetical", "coffee_count"],
            default="alphabetical",
        ),
    ]
)

VANISHING_CATEGORY = SettingCategory(
    key="vanishing",
    label="Vanishing Messages",
    description="Control when messages disappear from chat",
    icon=ICON_MAP["vanishing"],
    settings=[
        Setting(
            key="vanishing_enabled",
            label="Vanishing Messages",
            description="Enable automatic message disappearance",
            type=SettingType.TOGGLE,
            target="user",
            default=True,
        ),
        Setting(
            key="vanishing_threshold",
            label="Vanish After",
            description="Number of messages before a message vanishes",
            type=SettingType.NUMBER,
            target="user",
            min_value=1,
            max_value=10,
            default=2,
        ),
    ]
)

# Mixed (User + App Level) Settings Categories

NOTIFICATIONS_CATEGORY = SettingCategory(
    key="notifications",
    label="Notifications",
    description="Configure how and when you receive notifications",
    icon=ICON_MAP["notifications"],
    settings=[
        # App-level
        Setting(
            key="notifications_enabled",
            label="Notifications",
            description="Enable notifications for the whole app",
            type=SettingType.TOGGLE,
            target="app",
            section="notifications",
            default=True,
        ),
        Setting(
            key="notifications_silent",
            label="Silent Notifications",
            description="Send notifications without sound by default",
            type=SettingType.TOGGLE,
            target="app",
            section="notifications",
            default=False,
        ),
        # User-level
        Setting(
            key="notifications_enabled",
            label="Notifications",
            description="Receive notifications from other users",
            type=SettingType.TOGGLE,
            target="user",
            default=True,
        ),
        Setting(
            key="notifications_silent",
            label="Silent Notifications",
            description="Receive notifications without sound",
            type=SettingType.TOGGLE,
            target="user",
            default=False,
        ),
    ]
)

# App-Level Settings Categories

LOGGING_CATEGORY = SettingCategory(
    key="logging",
    label="Logging",
    description="Configure logging level, output format, and module-specific overrides",
    icon=ICON_MAP["logging"],
    settings=[
        Setting(
            key="log_level",
            label="Log Level",
            description="Global logging level: TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL",
            type=SettingType.ENUM,
            target="app",
            section="logging",
            options=["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            default="INFO",
            enum_behavior="select",
            enum_columns=3,
        ),
        Setting(
            key="log_show_time",
            label="Show Timestamp",
            description="Include timestamp in log messages",
            type=SettingType.TOGGLE,
            target="app",
            section="logging",
            default=False,
        ),
        Setting(
            key="log_show_caller",
            label="Show Caller Info",
            description="Include [filename:function] in log messages",
            type=SettingType.TOGGLE,
            target="app",
            section="logging",
            default=False,
        ),
        Setting(
            key="log_show_class",
            label="Show Class Name",
            description="Include [ClassName] tags in log messages",
            type=SettingType.TOGGLE,
            target="app",
            section="logging",
            default=True,
        ),
        # Special custom handlers (not shown in generic menus)
        Setting(
            key="log_format",
            label="Log Format Editor",
            description="Edit custom logging format with live preview",
            type=SettingType.CUSTOM,
            target="app",
            section="logging",
            custom_route_id="logging_format",
        ),
        Setting(
            key="log_modules",
            label="Module-Specific Overrides",
            description="Set different log levels for specific modules",
            type=SettingType.CUSTOM,
            target="app",
            section="logging",
            custom_route_id="logging_modules",
        ),
    ],
    subcategories=[
        SettingSubcategory(
            key="logging_format",
            label="Logging Format",
            description="Toggle the components shown in each log line",
            icon=ICON_MAP.get("logging_format"),
            setting_keys=["log_show_time", "log_show_caller", "log_show_class"],
            custom_route_id="logging_format",
        ),
        SettingSubcategory(
            key="logging_modules",
            label="Module-Specific Overrides",
            description="Override log levels per module",
            icon=ICON_MAP.get("logging_modules"),
            custom_route_id="logging_modules",
        ),
    ],
)

GSHEET_CATEGORY = SettingCategory(
    key="gsheet",
    label="Google Sheets Sync",
    description="Configure synchronization with Google Sheets",
    icon=ICON_MAP["gsheet"],
    settings=[
        Setting(
            key="periodic_sync_enabled",
            label="Periodic Sync",
            description="Enable automatic periodic synchronization with Google Sheets",
            type=SettingType.TOGGLE,
            target="app",
            section="gsheet",
            default=False,
        ),
        Setting(
            key="sync_period_minutes",
            label="Sync Interval (minutes)",
            description="How often to sync with Google Sheets",
            type=SettingType.NUMBER,
            target="app",
            section="gsheet",
            min_value=1,
            max_value=24 * 60,
            default=10,
        ),
        Setting(
            key="two_way_sync_enabled",
            label="Two-Way Sync",
            description="Enable two-way sync for paid amounts on completed cards",
            type=SettingType.TOGGLE,
            target="app",
            section="gsheet",
            default=False,
        ),
        Setting(
            key="sync_after_actions_enabled",
            label="Sync After Actions",
            description="Trigger sync after state-changing actions (order placed, debt paid, etc.)",
            type=SettingType.TOGGLE,
            target="app",
            section="gsheet",
            default=True,
        ),
    ]
)

SNAPSHOTS_CATEGORY = SettingCategory(
    key="snapshots",
    label="Snapshots",
    description="Configure snapshot retention and creation points",
    icon=ICON_MAP["snapshots"],
    settings=[
        Setting(
            key="keep_last",
            label="Snapshots to Keep",
            description="Number of committed snapshots to retain (older ones are pruned)",
            type=SettingType.NUMBER,
            target="app",
            section="snapshots",
            min_value=1,
            max_value=200,
            default=10,
        ),
        Setting(
            key="weekly_full_snapshot",
            label="Weekly Full Snapshots",
            description="Create a permanent full snapshot roughly once per week",
            type=SettingType.TOGGLE,
            target="app",
            section="snapshots",
            default=True,
        ),
        Setting(
            key="card_closed",
            label="Snapshot on Card Close",
            description="Create snapshot when a card is manually closed",
            type=SettingType.TOGGLE,
            target="app",
            section="snapshots",
            default=True,
        ),
        Setting(
            key="session_completed",
            label="Snapshot on Session Complete",
            description="Create snapshot when a session is completed",
            type=SettingType.TOGGLE,
            target="app",
            section="snapshots",
            default=True,
        ),
        Setting(
            key="quick_order",
            label="Snapshot on Quick Order",
            description="Create snapshot for quick-order flow",
            type=SettingType.TOGGLE,
            target="app",
            section="snapshots",
            default=True,
        ),
        Setting(
            key="card_created",
            label="Snapshot on Card Create",
            description="Create snapshot when a new card is manually created",
            type=SettingType.TOGGLE,
            target="app",
            section="snapshots",
            default=True,
        ),
    ],
    subcategories=[
        SettingSubcategory(
            key="snapshot_retention",
            label="Retention",
            description="Control how many snapshots are kept",
            icon=ICON_MAP.get("snapshot_retention"),
            setting_keys=["keep_last"],
        ),
        SettingSubcategory(
            key="snapshot_schedule",
            label="Schedule",
            description="Control periodic snapshot creation",
            icon=ICON_MAP.get("snapshot_schedule"),
            setting_keys=["weekly_full_snapshot"],
        ),
        SettingSubcategory(
            key="snapshot_reasons",
            label="Snapshot Reasons",
            description="Choose when automatic snapshots are created",
            icon=ICON_MAP.get("snapshot_reasons"),
            setting_keys=[
                "card_closed",
                "session_completed",
                "quick_order",
                "card_created",
            ],
            layout="grid_2",
            button_style="compact_toggle",
        ),
    ],
)

DEBTS_CATEGORY = SettingCategory(
    key="debts",
    label="Debt Management",
    description="Configure debt calculation and creditor handling",
    icon=ICON_MAP["debts"],
    settings=[
        Setting(
            key="correction_method",
            label="Correction Method",
            description="How to distribute missing-coffee correction: `absolute` (equal share) or `proportional` (by coffee count)",
            type=SettingType.ENUM,
            target="app",
            section="debt",
            options=["absolute", "proportional"],
            default="absolute",
        ),
        Setting(
            key="correction_threshold",
            label="Correction Threshold",
            description="Users must have at least this many coffees per card to participate in correction (0-50)",
            type=SettingType.NUMBER,
            target="app",
            section="debt",
            min_value=0,
            max_value=50,
            default=3,
        ),
        Setting(
            key="creditor_exempt_from_correction",
            label="Creditor Exempt",
            description="Exclude the purchaser (creditor) from missing-coffee correction calculations",
            type=SettingType.TOGGLE,
            target="app",
            section="debt",
            default=True,
        ),
        Setting(
            key="creditor_free_coffees",
            label="Creditor Free Coffees",
            description="Number of free coffees creditor may consume (cost distributed to others on card close)",
            type=SettingType.NUMBER,
            target="app",
            section="debt",
            min_value=0,
            max_value=200,
            default=0,
        ),
    ],
    subcategories=[
        SettingSubcategory(
            key="debt_correction",
            label="Debt Correction",
            description="Configure how missing-coffee corrections are calculated",
            icon=ICON_MAP.get("debt_correction"),
            setting_keys=["correction_method", "correction_threshold"],
        ),
        SettingSubcategory(
            key="creditor_royalties",
            label="Creditor Royalties",
            description="Configure creditor exemptions and free coffees",
            icon=ICON_MAP.get("creditor_royalties"),
            setting_keys=["creditor_exempt_from_correction", "creditor_free_coffees"],
        ),
    ],
)


# ===========================
# Menu Hierarchy & Structure
# ===========================

CATEGORIES: Dict[str, SettingCategory] = {
    # User-level categories (appear in main settings menu)
    "ordering": ORDERING_CATEGORY,
    "vanishing": VANISHING_CATEGORY,
    
    # Mixed categories (can appear in both menus)
    "notifications": NOTIFICATIONS_CATEGORY,
    
    # App-level categories (appear in admin settings menu)
    "logging": LOGGING_CATEGORY,
    "gsheet": GSHEET_CATEGORY,
    "snapshots": SNAPSHOTS_CATEGORY,
    "debts": DEBTS_CATEGORY,
}

MAIN_MENU = SettingsMenu(
    label="Your Settings",
    icon=ICON_MAP["main"],
    description="Personalize your experience",
    categories=["ordering", "vanishing", "notifications"],
)

ADMIN_MENU = SettingsMenu(
    label="Admin Settings",
    icon=ICON_MAP["admin"],
    description="Configure application-wide settings",
    categories=["notifications", "logging", "gsheet", "snapshots", "debts"],
)


# ===========================
# Helper Functions
# ===========================

def get_category(key: str) -> Optional[SettingCategory]:
    """Get a category definition by key."""
    return CATEGORIES.get(key)


def get_setting(category_key: str, setting_key: str, scope: Optional[str] = None) -> Optional[Setting]:
    """
    Get a setting definition.
    
    Args:
        category_key: Category key (e.g., "logging")
        setting_key: Setting key (e.g., "log_level")
        scope: Optional scope hint ("main" for user-target, "admin" for app-target)
    
    Returns:
        Setting object if found, None otherwise
    """
    category = get_category(category_key)
    if not category:
        return None
    
    settings_for_target = []
    
    # If scope specified, prefer scoped matches
    if scope == "main":
        settings_for_target = [s for s in category.settings if s.target == "user"]
    elif scope == "admin":
        settings_for_target = [s for s in category.settings if s.target == "app"]
    else:
        settings_for_target = category.settings
    
    for setting in settings_for_target:
        if setting.key == setting_key:
            return setting
    
    # Fall back to any match if scope didn't find one
    if scope:
        for setting in category.settings:
            if setting.key == setting_key:
                return setting
    
    return None


def get_category_settings_for_menu(category_key: str, target: str) -> List[Setting]:
    """
    Get all settings for a category filtered by target.
    
    Args:
        category_key: Category key
        target: "user" or "app"
    
    Returns:
        List of settings matching the target
    """
    category = get_category(category_key)
    if not category:
        return []
    return [s for s in category.settings if s.target == target]


def get_all_fields_for_model(target: str) -> Dict[str, Setting]:
    """
    Get all settings that should be fields in a model.
    
    Args:
        target: "user" or "app"
    
    Returns:
        Dict mapping field name -> Setting definition
    """
    result = {}
    for category in CATEGORIES.values():
        for setting in category.settings:
            if setting.target == target:
                result[setting.key] = setting
    return result


__all__ = [
    # Types
    "SettingType",
    "ValidatorType",
    "Validator",
    "Setting",
    "SettingCategory",
    "SettingSubcategory",
    "SettingsMenu",
    # Data
    "ICON_MAP",
    "SECTION_REPO_MAP",
    "CATEGORIES",
    "MAIN_MENU",
    "ADMIN_MENU",
    # Helpers
    "get_category",
    "get_setting",
    "get_category_settings_for_menu",
    "get_all_fields_for_model",
]
