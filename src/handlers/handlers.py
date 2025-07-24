from typing import TYPE_CHECKING
from fastapi import Depends, HTTPException

from ..common.log import log_user_login_attempt, log_user_login_success, log_admin_verification

if TYPE_CHECKING:
    from ..database.base_repo import BaseRepository


async def get_all_users(repo: "BaseRepository"):
    users = await repo.find_all_users()

    # if not users:
    #     raise KeyError("No users found")
    
    return users


async def check_user(id, repo: "BaseRepository"):
    log_user_login_attempt(id)
    user = await repo.find_user_by_id(id)
    
    if user:
        log_user_login_success(id, user.username if hasattr(user, 'username') else None)
        return True
    else:
        return False
    
async def check_password(password_input, repo: "BaseRepository"):
    password = await repo.get_password()
    if password is None:
        return False
    is_correct = password.verify_password(password_input)
    
    return is_correct
    
async def is_admin(id, repo: "BaseRepository"):
    admins: list = await repo.get_admins() or []
    is_admin_user = id in admins
    log_admin_verification(id, is_admin_user)
    
    return is_admin_user



async def register_user(id, repo: "BaseRepository"):
    pass

    # TODO: ask for password and if correct register user in database
