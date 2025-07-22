from fastapi import Depends, HTTPException

from ..database.base_repo import BaseRepository


async def get_all_users(repo: BaseRepository):
    users = await repo.find_all_users()

    # if not users:
    #     raise KeyError("No users found")
    
    return users


async def check_user(id, repo: BaseRepository):
    print("called check_user_handler with id:", id, "    on repo:", repo)
    user = await repo.find_user_by_id(id)
    print("found user:", user)
    
    if user:
        return True
    else:
        return False
    
async def check_password(password_input, repo: BaseRepository):
    print("handlers - check_password: password_input:", password_input)
    password = await repo.get_password()
    print("handlers - check_password: ", password)
    if password is None:
        return False
    is_correct = password.verify_password(password_input)
    
    return is_correct
    
async def is_admin(id, repo: BaseRepository):
    admins: list = await repo.get_admins() or []
    print("handlerss - is_admin - admins:", admins)
    if id in admins:
        return True
    else:
        return False



async def register_user(id, repo: BaseRepository):
    pass

    # TODO: ask for password and if correct register user in database
