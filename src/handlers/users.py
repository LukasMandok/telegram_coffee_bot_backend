from typing import TYPE_CHECKING, Optional
from fastapi import Depends, HTTPException

from ..common.log import Logger
from ..dependencies.dependencies import get_repo, repo

if TYPE_CHECKING:
    from ..database.base_repo import BaseRepository

logger = Logger("Handlers")


async def get_all_users(repo: "BaseRepository"):
    users = await repo.find_all_users()

    # if not users:
    #     raise KeyError("No users found")
    
    return users

@repo
async def check_user(repo: "BaseRepository", user_id: int):
    logger.debug(f"check_user (user_id={user_id})", extra_tag="AUTH")
    user = await repo.find_user_by_id(user_id)
    
    if user:
        logger.debug(f"user_found (user_id={user_id}, username={user.username})", extra_tag="AUTH")
        return True
    else:
        logger.debug(f"user_not_found (user_id={user_id})", extra_tag="AUTH")
        return False

@repo
async def check_password(repo: "BaseRepository", password_input):
    password = await repo.get_password()
    if password is None:
        logger.warning("No password found in database")
        return False
    is_correct = password.verify_password(password_input)
    logger.debug(f"Password verification: {'correct' if is_correct else 'incorrect'}")
    
    return is_correct
    
@repo
async def is_admin(repo: "BaseRepository", id):
    """Check if a user is an admin by looking up their user_id in config."""
    is_admin_user = await repo.is_user_admin(id)
    logger.debug(f"admin_check (user_id={id}, is_admin={is_admin_user})", extra_tag="AUTH")
    return is_admin_user


@repo
async def get_admin_list(repo: "BaseRepository"):
    """Get the list of all admin user IDs."""
    return await repo.get_admins()

@repo
async def add_admin(repo: "BaseRepository", user_id: int):
    """Add a user to the admin list."""
    return await repo.add_admin(user_id)

@repo
async def remove_admin(repo: "BaseRepository", user_id: int):
    """Remove a user from the admin list."""
    return await repo.remove_admin(user_id)

@repo
async def register_user(repo: "BaseRepository", user_id: int, username: str, first_name: str, last_name: Optional[str] = None, phone: Optional[str] = None, photo_id: Optional[int] = None, lang_code: str = "en", paypal_link: Optional[str] = None):
    """Register a new TelegramUser in the database with smart display name generation."""
    try:
        # Create the telegram user in the database with smart display name
        new_user = await repo.create_telegram_user(
            user_id=user_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            photo_id=photo_id,
            lang_code=lang_code,
            paypal_link=paypal_link,
        )
        return new_user
    
    except Exception as e:
        logger.error(
            f"register_user failed (user_id={user_id}, username={username})",
            extra_tag="DB",
            exc=e,
        )
        raise

@repo
async def create_passive_user(repo: "BaseRepository", first_name: str, last_name: Optional[str] = None):
    """Create a new PassiveUser in the database with smart display name generation."""
    try:
        # Create the passive user in the database with smart display name
        new_user = await repo.create_passive_user(
            first_name=first_name,
            last_name=last_name
        )
        return new_user
    
    except Exception as e:
        logger.error(
            f"create_passive_user failed (first_name={first_name}, last_name={last_name})",
            extra_tag="DB",
            exc=e,
        )
        raise

@repo
async def find_passive_user_by_name(repo: "BaseRepository", first_name: str, last_name: Optional[str] = None):
    """Find a passive user by name."""
    try:
        return await repo.find_passive_user_by_name(first_name, last_name)
    except Exception as e:
        logger.error(
            f"find_passive_user_by_name failed (first_name={first_name}, last_name={last_name})",
            extra_tag="DB",
            exc=e,
        )
        return None


@repo
async def convert_passive_to_telegram_user(repo: "BaseRepository", passive_user, user_id: int, username: str, first_name: str, last_name: Optional[str] = None, phone: Optional[str] = None, photo_id: Optional[int] = None, lang_code: str = "en", paypal_link: Optional[str] = None):
    """Convert a PassiveUser to a TelegramUser."""
    try:
        return await repo.convert_passive_to_telegram_user(
            passive_user=passive_user,
            user_id=user_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            photo_id=photo_id,
            lang_code=lang_code,
            paypal_link=paypal_link,
        )
    except Exception as e:
        logger.error(
            f"convert_passive_to_telegram_user failed (user_id={user_id}, username={username})",
            extra_tag="DB",
            exc=e,
        )
        raise