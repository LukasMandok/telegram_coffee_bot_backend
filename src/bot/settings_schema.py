from __future__ import annotations
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SettingType(str, Enum):
    TOGGLE = "toggle"
    NUMBER = "number"
    ENUM = "enum"


class Setting(BaseModel):
    key: str  # key used when calling repo getters/updates (for app-level) or attribute name (for user-level)
    label: str
    description: Optional[str] = None
    type: SettingType
    target: str = Field(..., description="'app' or 'user'")
    section: Optional[str] = Field(None, description="App section (e.g. 'logging','gsheet'). For user-target use None.")
    # Additional constraints
    min_value: Optional[int] = None
    max_value: Optional[int] = None
    options: Optional[List[str]] = None
    default: Optional[Any] = None


# Categories map human category key -> metadata including label and list of settings
CATEGORIES: Dict[str, Dict] = {
    "ordering": {
        "label": "Ordering Settings",
        "settings": [
            Setting(key="group_page_size", label="Group Page Size", type=SettingType.NUMBER, target="user", description="Number of users per page", min_value=5, max_value=20, default=10),
            Setting(key="group_sort_by", label="Group Sorting", type=SettingType.ENUM, target="user", description="How to sort users", options=["alphabetical", "coffee_count"], default="alphabetical"),
        ],
    },

    "vanishing": {
        "label": "Vanishing Messages",
        "settings": [
            Setting(key="vanishing_enabled", label="Vanishing Messages", type=SettingType.TOGGLE, target="user", description="Enable vanishing messages", default=True),
            Setting(key="vanishing_threshold", label="Vanish Threshold", type=SettingType.NUMBER, target="user", description="Messages until vanish", min_value=1, max_value=10, default=2),
        ],
    },

    "notifications": {
        "label": "Notifications",
        "settings": [
            # App-level
            Setting(key="notifications_enabled", label="Global Enabled", type=SettingType.TOGGLE, target="app", section="notifications", description="Global notifications enabled", default=True),
            Setting(key="notifications_silent", label="Global Silent", type=SettingType.TOGGLE, target="app", section="notifications", description="Send notifications silently by default", default=False),
            # User-level
            Setting(key="notifications_enabled", label="Your Enabled", type=SettingType.TOGGLE, target="user", description="Receive notifications" , default=True),
            Setting(key="notifications_silent", label="Your Silent", type=SettingType.TOGGLE, target="user", description="Receive notifications silently" , default=False),
        ],
    },

    "logging": {
        "label": "Logging",
        "settings": [
            Setting(key="log_level", label="Log Level", type=SettingType.ENUM, target="app", section="logging", options=["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], default="INFO"),
            Setting(key="log_show_time", label="Show Timestamp", type=SettingType.TOGGLE, target="app", section="logging", default=False),
            Setting(key="log_show_caller", label="Show Caller", type=SettingType.TOGGLE, target="app", section="logging", default=False),
            Setting(key="log_show_class", label="Show Class", type=SettingType.TOGGLE, target="app", section="logging", default=True),
        ],
    },

    "gsheet": {
        "label": "Google Sheets",
        "settings": [
            Setting(key="periodic_sync_enabled", label="Periodic Sync", type=SettingType.TOGGLE, target="app", section="gsheet", default=False),
            Setting(key="sync_period_minutes", label="Sync Period (min)", type=SettingType.NUMBER, target="app", section="gsheet", min_value=1, max_value=24*60, default=10),
            Setting(key="two_way_sync_enabled", label="Two-way Sync", type=SettingType.TOGGLE, target="app", section="gsheet", default=False),
        ],
    },

    "snapshots": {
        "label": "Snapshots",
        "settings": [
            Setting(key="keep_last", label="Keep Last", type=SettingType.NUMBER, target="app", section="snapshots", min_value=1, max_value=200, default=10),
            Setting(key="card_closed", label="Snapshot on Card Close", type=SettingType.TOGGLE, target="app", section="snapshots", default=True),
            Setting(key="session_completed", label="Snapshot on Session Complete", type=SettingType.TOGGLE, target="app", section="snapshots", default=True),
            Setting(key="quick_order", label="Snapshot on Quick Order", type=SettingType.TOGGLE, target="app", section="snapshots", default=True),
            Setting(key="card_created", label="Snapshot on Card Create", type=SettingType.TOGGLE, target="app", section="snapshots", default=True),
        ],
    },

    "debts": {
        "label": "Debts",
        "settings": [
            Setting(key="correction_method", label="Correction Method", type=SettingType.ENUM, target="app", section="debt", options=["absolute", "proportional"], default="absolute"),
            Setting(key="correction_threshold", label="Correction Threshold", type=SettingType.NUMBER, target="app", section="debt", min_value=0, max_value=50, default=3),
            Setting(key="creditor_exempt_from_correction", label="Creditor Exempt", type=SettingType.TOGGLE, target="app", section="debt", default=True),
            Setting(key="creditor_free_coffees", label="Creditor Free Coffees", type=SettingType.NUMBER, target="app", section="debt", min_value=0, max_value=200, default=0),
        ],
    },
}


__all__ = ["CATEGORIES", "SettingType", "Setting"]
