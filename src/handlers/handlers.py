from typing import TYPE_CHECKING, Optional
from fastapi import Depends, HTTPException

from ..common.log import log_user_login_attempt, log_user_login_success, log_admin_verification
from ..dependencies.dependencies import get_repo

from ..common.log import log_database_error

if TYPE_CHECKING:
    from ..database.base_repo import BaseRepository


async def get_all_users(repo: "BaseRepository"):
    users = await repo.find_all_users()

    # if not users:
    #     raise KeyError("No users found")
    
    return users


async def check_user(user_id: int, repo: "BaseRepository"):
    log_user_login_attempt(user_id)
    user = await repo.find_user_by_id(user_id)
    
    print(f"handlers: check_user - user found: {user}")
    
    if user:
        log_user_login_success(user_id, user.username if hasattr(user, 'username') else None)
        return True
    else:
        return False
    
async def check_password(password_input, repo: "BaseRepository"):
    print(f"handlers: check_password - input: '{password_input}'")
    password = await repo.get_password()
    print(f"handlers: check_password - retrieved password object: {password}")
    if password is None:
        print("handlers: check_password - no password found in database")
        return False
    is_correct = password.verify_password(password_input)
    print(f"handlers: check_password - verification result: {is_correct}")
    
    return is_correct
    
async def is_admin(id, repo: "BaseRepository"):
    """Check if a user is an admin by looking up their user_id in config."""
    is_admin_user = await repo.is_user_admin(id)
    log_admin_verification(id, is_admin_user)
    return is_admin_user


async def get_admin_list(repo: "BaseRepository"):
    """Get the list of all admin user IDs."""
    return await repo.get_admins()


async def add_admin(user_id: int, repo: "BaseRepository"):
    """Add a user to the admin list."""
    return await repo.add_admin(user_id)


async def remove_admin(user_id: int, repo: "BaseRepository"):
    """Remove a user from the admin list."""
    return await repo.remove_admin(user_id)



async def register_user(user_id: int, username: str, first_name: str, last_name: Optional[str] = None, phone: Optional[str] = None, photo_id: Optional[int] = None, lang_code: str = "en", repo: Optional["BaseRepository"] = None):
    """Register a new FullUser in the database with smart display name generation."""
    if repo is None:
        repo = get_repo()
    
    try:
        # Create the full user in the database with smart display name
        new_user = await repo.create_full_user(
            user_id=user_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            photo_id=photo_id,
            lang_code=lang_code
        )
        return new_user
    
    except Exception as e:
        log_database_error("register_user", str(e), {"user_id": user_id})
        raise


async def create_passive_user(first_name: str, last_name: Optional[str] = None, repo: Optional["BaseRepository"] = None):
    """Create a new PassiveUser in the database with smart display name generation."""
    if repo is None:
        repo = get_repo()
    
    try:
        # Create the passive user in the database with smart display name
        new_user = await repo.create_passive_user(
            first_name=first_name,
            last_name=last_name
        )
        return new_user
    
    except Exception as e:
        log_database_error("create_passive_user", str(e), {"first_name": first_name, "last_name": last_name})
        raise


async def find_passive_user_by_name(first_name: str, last_name: Optional[str] = None, repo: Optional["BaseRepository"] = None):
    """Find a passive user by name."""
    if repo is None:
        repo = get_repo()
    
    try:
        return await repo.find_passive_user_by_name(first_name, last_name)
    except Exception as e:
        log_database_error("find_passive_user_by_name", str(e), {"first_name": first_name, "last_name": last_name})
        return None


async def convert_passive_to_full_user(passive_user, user_id: int, username: str, first_name: str, last_name: Optional[str] = None, phone: Optional[str] = None, photo_id: Optional[int] = None, lang_code: str = "en", repo: Optional["BaseRepository"] = None):
    """Convert a PassiveUser to a FullUser."""
    if repo is None:
        repo = get_repo()
    
    try:
        return await repo.convert_passive_to_full_user(
            passive_user=passive_user,
            user_id=user_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            photo_id=photo_id,
            lang_code=lang_code
        )
    except Exception as e:
        log_database_error("convert_passive_to_full_user", str(e), {"user_id": user_id})
        raise
