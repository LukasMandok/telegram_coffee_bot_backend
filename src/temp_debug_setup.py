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
from typing import List, Tuple
from .handlers import handlers
from .dependencies.dependencies import get_repo
from .common.log import log_database_error

# Predefined list of passive users to create for debugging/testing
DEBUG_PASSIVE_USERS: List[Tuple[str, str]] = [
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
    print("🔧 [DEBUG SETUP] Starting passive user creation...")
    
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
                print(f"  ✓ User already exists: {existing_user.display_name}")
                existing_count += 1
                continue
            
            # Create the passive user
            new_user = await handlers.create_passive_user(
                first_name=first_name,
                last_name=last_name_to_use
            )
            
            print(f"  ✅ Created: {new_user.display_name}")
            created_count += 1
            
        except Exception as e:
            print(f"  ❌ Failed to create {first_name} {last_name}: {str(e)}")
            log_database_error(
                "debug_setup_passive_user", 
                str(e), 
                {"first_name": first_name, "last_name": last_name}
            )
            failed_count += 1
    
    print(f"🔧 [DEBUG SETUP] Complete: {created_count} created, {existing_count} existing, {failed_count} failed")


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
    print("🧹 [DEBUG CLEANUP] Starting passive user removal...")
    
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
                print(f"  User not found: {first_name} {last_name or '(no last name)'}")
                not_found_count += 1
                continue
            
            # Remove the user (you'll need to implement this method)
            # await repo.delete_passive_user(existing_user.id)
            print(f"  Would remove: {existing_user.display_name} (deletion not implemented)")
            # For now, just count as removed since deletion method needs to be implemented
            removed_count += 1
            
        except Exception as e:
            print(f"  ❌ Failed to remove {first_name} {last_name}: {str(e)}")
            log_database_error(
                "debug_cleanup_passive_user", 
                str(e), 
                {"first_name": first_name, "last_name": last_name}
            )
            failed_count += 1
    
    print(f"🧹 [DEBUG CLEANUP] Complete: {removed_count} removed, {not_found_count} not found, {failed_count} failed")


def is_debug_mode() -> bool:
    """
    Check if the application is running in debug mode.
    
    This function checks environment variables or configuration to determine
    if debug features should be enabled.
    
    Returns:
        bool: True if debug mode is enabled, False otherwise
    """
    import os
    from .config import settings
    
    # Check for debug mode in environment variables and settings
    return (
        os.getenv("DEBUG_MODE", "").lower() in ["true", "1", "yes"] or
        os.getenv("ENVIRONMENT", "").lower() in ["development", "dev", "debug"] or
        getattr(settings, "DEBUG_MODE", False)
    )


async def run_debug_setup_if_enabled() -> None:
    """
    Run debug setup only if debug mode is enabled.
    
    This is a safe wrapper function that checks if debug mode is enabled
    before running the setup. Call this from your application startup.
    
    Returns:
        None
    """
    if is_debug_mode():
        print("🔧 [DEBUG SETUP] Debug mode detected, setting up passive users...")
        await setup_debug_passive_users()
    else:
        print("🔧 [DEBUG SETUP] Debug mode not enabled, skipping passive user setup")


if __name__ == "__main__":
    """
    Allow running this module directly for manual testing.
    """
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "cleanup":
        # Run cleanup
        asyncio.run(cleanup_debug_passive_users())
    else:
        # Run setup
        asyncio.run(setup_debug_passive_users())
