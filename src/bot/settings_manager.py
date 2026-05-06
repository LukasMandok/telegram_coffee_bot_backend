"""Settings manager for non-UI settings operations."""

import logging

from ..common.log import log_settings
from ..dependencies.dependencies import get_repo

logger = logging.getLogger(__name__)


class SettingsManager:
    """Business-logic helper for settings initialization."""

    @classmethod
    async def initialize_log_settings_from_db(cls) -> None:
        """Load logging settings from the database and apply them to runtime."""
        try:
            repo = get_repo()
            db_log_settings = await repo.get_log_settings()

            if db_log_settings:
                log_settings.show_time = db_log_settings.get("log_show_time", True)
                log_settings.show_caller = db_log_settings.get("log_show_caller", True)
                log_settings.show_class = db_log_settings.get("log_show_class", True)
                log_settings.level = db_log_settings.get("log_level", "INFO")

                log_settings.module_overrides = dict(db_log_settings.get("log_module_overrides", {}) or {})

                logger.info(
                    "Initialized log settings: level=%s, time=%s, caller=%s, class=%s",
                    log_settings.level,
                    log_settings.show_time,
                    log_settings.show_caller,
                    log_settings.show_class,
                )
            else:
                logger.warning("No log settings found in database, using defaults")
        except Exception as exc:
            logger.error("Failed to initialize log settings: %s", exc, exc_info=exc)
