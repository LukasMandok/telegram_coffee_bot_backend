"""
Settings Model Definitions

Beanie Document models derived from settings_schema.

This module lives next to the other Beanie models so the data layer stays in
one place. Field defaults and constraints are sourced from settings_schema via
beanie_settings_helper.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Dict, Optional

from beanie import Document, Indexed
from pydantic import BaseModel, Field, field_validator

from ..bot.settings_schema import get_setting
from .beanie_settings_helper import field_from_schema, validator_from_setting


LOG_LEVEL = get_setting("logging", "log_level")
LOG_SHOW_TIME = get_setting("logging", "log_show_time")
LOG_SHOW_CALLER = get_setting("logging", "log_show_caller")
LOG_SHOW_CLASS = get_setting("logging", "log_show_class")

NOTIFICATIONS_ENABLED = get_setting("notifications", "notifications_enabled")
NOTIFICATIONS_SILENT = get_setting("notifications", "notifications_silent")

DEBT_METHOD = get_setting("debts", "correction_method")
DEBT_THRESHOLD = get_setting("debts", "correction_threshold")
CREDITOR_EXEMPT = get_setting("debts", "creditor_exempt_from_correction")
CREDITOR_FREE_COFFEES = get_setting("debts", "creditor_free_coffees")

GSHEET_PERIODIC = get_setting("gsheet", "periodic_sync_enabled")
GSHEET_PERIOD = get_setting("gsheet", "sync_period_minutes")
GSHEET_TWO_WAY = get_setting("gsheet", "two_way_sync_enabled")
GSHEET_AFTER_ACTIONS = get_setting("gsheet", "sync_after_actions_enabled")

SNAPSHOT_KEEP_LAST = get_setting("snapshots", "keep_last")
SNAPSHOT_WEEKLY_FULL = get_setting("snapshots", "weekly_full_snapshot")
SNAPSHOT_CARD_CLOSED = get_setting("snapshots", "card_closed")
SNAPSHOT_SESSION_COMPLETED = get_setting("snapshots", "session_completed")
SNAPSHOT_QUICK_ORDER = get_setting("snapshots", "quick_order")
SNAPSHOT_CARD_CREATED = get_setting("snapshots", "card_created")

USER_GROUP_PAGE_SIZE = get_setting("ordering", "group_page_size")
USER_GROUP_SORT_BY = get_setting("ordering", "group_sort_by")
USER_VANISHING_ENABLED = get_setting("vanishing", "vanishing_enabled")
USER_VANISHING_THRESHOLD = get_setting("vanishing", "vanishing_threshold")
USER_NOTIFICATIONS_ENABLED = get_setting("notifications", "notifications_enabled", scope="main")
USER_NOTIFICATIONS_SILENT = get_setting("notifications", "notifications_silent", scope="main")


class LoggingSettings(BaseModel):
    """Logging configuration section."""

    level: str = field_from_schema(LOG_LEVEL)
    show_time: bool = field_from_schema(LOG_SHOW_TIME)
    show_caller: bool = field_from_schema(LOG_SHOW_CALLER)
    show_class: bool = field_from_schema(LOG_SHOW_CLASS)
    module_overrides: Dict[str, str] = Field(
        default_factory=dict,
        description="Per-module log overrides. Values are TRACE/DEBUG/INFO/WARNING/ERROR/CRITICAL/OFF",
    )

    @field_validator("level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        return validator_from_setting(
            LOG_LEVEL,
            "level",
            normalize=lambda item: (item or "").upper(),
        )(value)

    @field_validator("module_overrides")
    @classmethod
    def validate_module_overrides(cls, value: Dict[str, str]) -> Dict[str, str]:
        if not value:
            return {}

        valid_states = {"TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "OFF"}
        normalized: Dict[str, str] = {}
        for key, item in value.items():
            state = (item or "").strip().upper()
            if state not in valid_states:
                raise ValueError(f"Invalid module log override for '{key}': {item}")
            normalized[str(key)] = state
        return normalized


class NotificationSettings(BaseModel):
    """Notification configuration section."""

    enabled: bool = field_from_schema(NOTIFICATIONS_ENABLED)
    silent: bool = field_from_schema(NOTIFICATIONS_SILENT)


class DebtSettings(BaseModel):
    """Debt calculation configuration section."""

    correction_method: str = field_from_schema(DEBT_METHOD)
    correction_threshold: int = field_from_schema(DEBT_THRESHOLD)
    creditor_exempt_from_correction: bool = field_from_schema(CREDITOR_EXEMPT)
    creditor_free_coffees: int = field_from_schema(CREDITOR_FREE_COFFEES)

    @field_validator("correction_method")
    @classmethod
    def validate_correction_method(cls, value: str) -> str:
        return validator_from_setting(
            DEBT_METHOD,
            "correction_method",
            normalize=lambda item: (item or "").strip().lower(),
        )(value)

    @field_validator("correction_threshold")
    @classmethod
    def validate_correction_threshold(cls, value: Any) -> int:
        return validator_from_setting(DEBT_THRESHOLD, "correction_threshold")(value)

    @field_validator("creditor_free_coffees")
    @classmethod
    def validate_free_coffees(cls, value: Any) -> int:
        return validator_from_setting(CREDITOR_FREE_COFFEES, "creditor_free_coffees")(value)


class GsheetSettings(BaseModel):
    """Google Sheets synchronization settings."""

    periodic_sync_enabled: bool = field_from_schema(GSHEET_PERIODIC)
    sync_period_minutes: int = field_from_schema(GSHEET_PERIOD)
    two_way_sync_enabled: bool = field_from_schema(GSHEET_TWO_WAY)
    sync_after_actions_enabled: bool = field_from_schema(GSHEET_AFTER_ACTIONS)

    @field_validator("sync_period_minutes")
    @classmethod
    def validate_sync_period_minutes(cls, value: Any) -> int:
        return validator_from_setting(GSHEET_PERIOD, "sync_period_minutes")(value)


class SnapshotSettings(BaseModel):
    """Snapshot settings."""

    keep_last: int = field_from_schema(SNAPSHOT_KEEP_LAST)
    weekly_full_snapshot: bool = field_from_schema(SNAPSHOT_WEEKLY_FULL)
    card_closed: bool = field_from_schema(SNAPSHOT_CARD_CLOSED)
    session_completed: bool = field_from_schema(SNAPSHOT_SESSION_COMPLETED)
    quick_order: bool = field_from_schema(SNAPSHOT_QUICK_ORDER)
    card_created: bool = field_from_schema(SNAPSHOT_CARD_CREATED)

    @field_validator("keep_last")
    @classmethod
    def validate_keep_last(cls, value: Any) -> int:
        return validator_from_setting(SNAPSHOT_KEEP_LAST, "keep_last")(value)


class AppSettings(Document):
    """Global application settings organized into sections."""

    logging: LoggingSettings = Field(default_factory=LoggingSettings, description="Logging configuration")
    notifications: NotificationSettings = Field(default_factory=NotificationSettings, description="Notification configuration")
    debt: DebtSettings = Field(default_factory=DebtSettings, description="Debt calculation configuration")
    gsheet: GsheetSettings = Field(default_factory=GsheetSettings, description="Google Sheets sync settings")
    snapshots: SnapshotSettings = Field(default_factory=SnapshotSettings, description="Snapshot settings")

    class Settings:
        name = "app_settings"


class UserSettings(Document):
    """User-specific settings for a Telegram user."""

    user_id: Annotated[int, Indexed(unique=True)] = Field(..., description="Telegram user ID")

    group_page_size: int = field_from_schema(USER_GROUP_PAGE_SIZE)
    group_sort_by: str = field_from_schema(USER_GROUP_SORT_BY)
    vanishing_enabled: bool = field_from_schema(USER_VANISHING_ENABLED)
    vanishing_threshold: int = field_from_schema(USER_VANISHING_THRESHOLD)
    notifications_enabled: bool = field_from_schema(USER_NOTIFICATIONS_ENABLED)
    notifications_silent: bool = field_from_schema(USER_NOTIFICATIONS_SILENT)
    credit_overview_view_mode: str = Field(
        default="by_card",
        description="Credit overview view mode: 'by_card' or 'by_debtor'",
    )

    @field_validator("group_sort_by")
    @classmethod
    def validate_sort_by(cls, value: str) -> str:
        return validator_from_setting(USER_GROUP_SORT_BY, "group_sort_by")(value)

    @field_validator("group_page_size")
    @classmethod
    def validate_group_page_size(cls, value: Any) -> int:
        return validator_from_setting(USER_GROUP_PAGE_SIZE, "group_page_size")(value)

    @field_validator("vanishing_threshold")
    @classmethod
    def validate_vanishing_threshold(cls, value: Any) -> int:
        return validator_from_setting(USER_VANISHING_THRESHOLD, "vanishing_threshold")(value)

    @field_validator("credit_overview_view_mode")
    @classmethod
    def validate_credit_overview_view_mode(cls, value: str) -> str:
        if value not in ["by_card", "by_debtor"]:
            raise ValueError("credit_overview_view_mode must be 'by_card' or 'by_debtor'")
        return value

    class Settings:
        name = "user_settings"


__all__ = [
    "LoggingSettings",
    "NotificationSettings",
    "DebtSettings",
    "GsheetSettings",
    "SnapshotSettings",
    "AppSettings",
    "UserSettings",
]