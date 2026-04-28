"""
Temporary debug setup file for creating passive users during development.

This module provides helpers to create or remove passive users for
development/testing when `users` environment variable is set. There are
no built-in default users in this module; use the `users` env var to list
names or rely on the production `src.initial_setup` fallback.

Usage:
    1. Set `DEBUG_MODE=true` or `ENVIRONMENT=dev` in your environment
    2. Set `users` env var to a comma-separated list of names
       (e.g. users=[Lukas Mandok, Joey])
    3. Start the application normally - debug setup will create users only
       when `users` is provided.

    Or run manually:
    - Call `setup_debug_passive_users()` after database connection is established

Environment Variables:
    DEBUG_MODE=true         # Enable debug mode
    ENVIRONMENT=development # Alternative way to enable debug mode
    users=[First Last, SingleName, ...]  # Passive users to create for debug
"""

import asyncio
import os
import sys
from .dependencies.dependencies import get_repo
from .common.log import Logger, log_settings
from .config import app_config
from .models import beanie_models as models

logger = Logger("DebugSetup")

# Debug-only passive user creation will only use users configured via
# the `users` environment variable. If no env var is present, the
# debug setup will skip creating passive users (no built-in defaults).


# Debug passive-user creation helpers have been removed; debug setup
# now only manages development-only defaults. Use `src.initial_setup` for
# production first-start behavior and env-driven passive-user creation.


# cleanup helper removed


def is_debug_mode() -> bool:
    """
    Check if the application is running in debug mode.

    This function checks environment variables or configuration to determine
    if debug features should be enabled.

    Returns:
        bool: True if debug mode is enabled, False otherwise
    """
    # Check for debug mode in environment variables and app_config
    return (
        os.getenv("DEBUG_MODE", "").lower() in ["true", "1", "yes"] or
        os.getenv("ENVIRONMENT", "").lower() in ["development", "dev", "debug"] or
        getattr(app_config, "DEBUG_MODE", False)
    )


async def initialize_log_settings() -> None:
    """
    Initialize runtime log settings from database configuration.

    This function loads the logging settings from the AppSettings document
    and updates the runtime log settings accordingly. Should be called
    on application startup after database connection is established.

    Returns:
        None
    """
    try:
        repo = get_repo()
        db_log_settings = await repo.get_log_settings()

        if db_log_settings:
            # Update runtime settings
            log_settings.show_time = db_log_settings.get("log_show_time", False)
            log_settings.show_caller = db_log_settings.get("log_show_caller", False)
            log_settings.show_class = db_log_settings.get("log_show_class", True)
            log_settings.level = db_log_settings.get("log_level", "TRACE")

            logger.info(
                f"Initialized log settings: level={log_settings.level}, time={log_settings.show_time}, "
                f"caller={log_settings.show_caller}, class={log_settings.show_class}"
            )
        else:
            logger.warning("No log settings found in database, using defaults")

    except Exception as e:
        logger.error("Failed to initialize log settings", extra_tag="LOG", exc=e)


async def run_debug_setup_if_enabled() -> None:
    """
    Run debug setup only if debug mode is enabled.

    This is a safe wrapper function that checks if debug mode is enabled
    before running the setup. Call this from your application startup.

    Returns:
        None
    """
    # Only setup defaults and passive users if in debug mode
    if is_debug_mode():
        logger.info("Debug mode detected, setting up development defaults (passive-user creation handled via .env or initial_setup)")
        repo = get_repo()

        # IMPORTANT: do not reset defaults on every startup.
        # setup_defaults() deletes AppSettings/Config/Password and will wipe persisted admin settings.
        # Only run it when required (fresh DB) or explicitly forced.

        have_config = await models.Config.find_one() is not None
        have_password = await models.Password.find_one() is not None
        have_app_settings = await models.AppSettings.find_one() is not None

        if not (have_config and have_password and have_app_settings):
            await repo.setup_defaults()  # type: ignore - repo is BeanieRepository at runtime
        else:
            logger.info("Defaults already exist; skipping setup_defaults()")
    else:
        logger.debug("Debug mode not enabled, skipping debug setup")


if __name__ == "__main__":
    """
    Allow running this module directly for manual testing.
    """
    # Run debug-only setup (defaults) when invoked directly
    asyncio.run(run_debug_setup_if_enabled())
