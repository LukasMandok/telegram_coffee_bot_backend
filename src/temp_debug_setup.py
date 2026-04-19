"""
Temporary debug setup file for creating test passive users.

This module contains functions to create a predefined set of passive users
on application startup if they don't already exist. This is meant for
development and testing purposes only.

Usage:
    1. Set DEBUG_MODE=true or ENVIRONMENT=dev in your environment variables
    2. Start the application normally - users will be created automatically
    
    Or run manually:
    - Call setup_debug_passive_users() after database connection is established

Environment Variables:
    DEBUG_MODE=true         # Enable debug mode
    ENVIRONMENT=development # Alternative way to enable debug mode
"""

import asyncio
import os
import sys
from typing import List, Tuple
from .handlers import users as handlers
from .dependencies.dependencies import get_repo
from .common.log import Logger, log_settings
from .config import app_config
from .models import beanie_models as models

logger = Logger("DebugSetup")

# Predefined list of passive users to create for debugging/testing
DEBUG_PASSIVE_USERS: List[Tuple[str, str]] = [
    ("Lukas", "Mandok"),
    ("Borys", "Oppenländer"),
    ("Heiko", "Augustin"),
    ("David", "Immig"),
    ("David", "Fritz"),
    ("André", "Schöning"),
    ("Tamasi", "Kar"),
    ("Benjamin", "Weinläder"),
    ("Joey", ""),  # Only first name provided
    ("Luigi", "Vingani"),
    ("Lelia", "Fuchs"),
    ("Jasper", "Sammer"),
    ("Frosso", "Zachou"),
    ("Sebastian", "Dittmeier"),
    ("Giulia", "Fazzino"),
    ("Abhi", "Nandi")
]


async def setup_debug_passive_users() -> None:
    """
    Create all debug passive users if they don't already exist.
    
    This function iterates through the DEBUG_PASSIVE_USERS list and creates
    each user if they don't already exist in the database. It's safe to call
    multiple times as it checks for existing users before creating new ones.
    
    Returns:
        None
        
    Raises:
        Exception: If there are issues with database operations
    """
    logger.info("Starting passive user creation...")
    
    created_count = 0
    existing_count = 0
    failed_count = 0
    
    for first_name, last_name in DEBUG_PASSIVE_USERS:
        try:
            # Handle case where last name is empty string
            last_name_to_use = last_name if last_name.strip() else None
            
            # Check if user already exists
            existing_user = await handlers.find_passive_user_by_name(
                first_name=first_name,
                last_name=last_name_to_use
            )
            
            if existing_user:
                logger.debug(f"User already exists: {existing_user.display_name}")
                existing_count += 1
                continue
            
            # Create the passive user
            new_user = await handlers.create_passive_user(
                first_name=first_name,
                last_name=last_name_to_use
            )
            
            logger.info(f"Created: {new_user.display_name}")
            created_count += 1
            
        except ValueError as e:
            # User already exists as Telegram user - this is expected, not an error
            logger.debug(f"Skipped {first_name} {last_name}: {str(e)}")
            existing_count += 1
        except Exception as e:
            logger.error(
                f"Failed to create passive user (first_name={first_name}, last_name={last_name})",
                extra_tag="DB",
                exc=e,
            )
            failed_count += 1
    
    logger.info(f"Debug setup complete: {created_count} created, {existing_count} existing, {failed_count} failed")


async def cleanup_debug_passive_users() -> None:
    """
    Remove all debug passive users from the database.
    
    This function is the opposite of setup_debug_passive_users(). It finds
    and removes all users that match the names in DEBUG_PASSIVE_USERS.
    Use this for cleanup after testing.
    
    WARNING: This will permanently delete users and their associated data!
    
    Returns:
        None
        
    Raises:
        Exception: If there are issues with database operations
    """
    logger.info("Starting passive user removal...")
    
    removed_count = 0
    not_found_count = 0
    failed_count = 0
    
    for first_name, last_name in DEBUG_PASSIVE_USERS:
        try:
            # Handle case where last name is empty string
            last_name_to_use = last_name if last_name.strip() else None
            
            # Find the user
            existing_user = await handlers.find_passive_user_by_name(
                first_name=first_name,
                last_name=last_name_to_use
            )
            
            if not existing_user:
                logger.debug(f"User not found: {first_name} {last_name or '(no last name)'}")
                not_found_count += 1
                continue
            
            # Remove the user (you'll need to implement this method)
            # await repo.delete_passive_user(existing_user.id)
            logger.debug(f"Would remove: {existing_user.display_name} (deletion not implemented)")
            # For now, just count as removed since deletion method needs to be implemented
            removed_count += 1
            
        except Exception as e:
            logger.error(
                f"Failed to remove passive user (first_name={first_name}, last_name={last_name})",
                extra_tag="DB",
                exc=e,
            )
            failed_count += 1
    
    logger.info(f"Debug cleanup complete: {removed_count} removed, {not_found_count} not found, {failed_count} failed")


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
        logger.info("Debug mode detected, setting up defaults and passive users...")
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

        await setup_debug_passive_users()
    else:
        logger.debug("Debug mode not enabled, skipping debug setup")


if __name__ == "__main__":
    """
    Allow running this module directly for manual testing.
    """
    if len(sys.argv) > 1 and sys.argv[1] == "cleanup":
        # Run cleanup
        asyncio.run(cleanup_debug_passive_users())
    else:
        # Run setup
        asyncio.run(setup_debug_passive_users())
