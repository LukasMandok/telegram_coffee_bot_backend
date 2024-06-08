from fastapi import Depends

from ..database.base_repo import BaseRepository


async def get_all_users(repo: BaseRepository):
    users = await repo.find_all_users()

    # if not users:
    #     raise KeyError("No users found")
    
    return users


async def check_user(id, repo: BaseRepository):
    print("called check_user_handler with id:", id)
    print("!!!!!!!!! repo:", repo)
    user = await repo.find_user_by_id(id)
    print("found user:", user)
    if user:
        return True

async def check_password(password, repo: BaseRepository):
    hashed_password = await repo.get_password()
    is_correct = hashed_password.verify_password(password)
    
    return is_correct
    
async def is_admin(id, repo: BaseRepository):
    # TODO: implement
    return False
