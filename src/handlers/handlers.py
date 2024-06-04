from fastapi import Depends

from ..models.motormodels import *
from ..schemas.user import *

from ..database.base_repo import BaseRepository
from ..dependencies.dependencies import get_repo


def get_all_users(repo: BaseRepository = Depends(get_repo)):
    users = repo.find_all_users()

    # if not users:
    #     raise KeyError("No users found")
    
    return users


def check_user(id, repo: BaseRepository = Depends(get_repo)):
    user = repo.find_user_by_id(id)
    if user:
        return True

def check_password(password, repo: BaseRepository = Depends(get_repo)):
    hashed_password = repo.get_password()
    
    
def is_admin(id, repo: BaseRepository = Depends(get_repo)):
    # TODO: implement
    return False
