from typing import Optional
from typing_extensions import Annotated

from fastapi import Header, HTTPException, Depends
from fastapi.security import APIKeyQuery

from ..models.base_models import *
from ..database.base_repo import BaseRepository
from ..database.beanie_repo import BeanieRepository


# TODO: We need to get the instance not a class here
def get_repo() -> BaseRepository:
    return BeanieRepository()


api_key_query = APIKeyQuery(name="token", auto_error=False)
# q: what does this do?
# a


# async def get_current_user(
#     api_key: Optional[str] = Depends(api_key_query)
# ):
    
    
    




### Veryfy if user and admin are in database

async def verify_username(id: Annotated[int, Header()]):
    if not await get_repo().find_user_by_id(id):
        raise HTTPException(status_code=400, detail="Username is not registered.")
    
async def verify_admin(id: Annotated[int, Header()]):
    if not id in await get_repo().get_admins():
        raise HTTPException(status_code=400, detail="User is not an admin.")
    
    
    
### TODO: Actually verify a meaningfull token
    
async def verify_token(token: Annotated[str, Header()]):
    if token != "token123":
        raise HTTPException(status_code=400, detail="Token header invalid")