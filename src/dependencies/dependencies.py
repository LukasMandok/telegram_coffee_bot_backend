from functools import wraps
from typing import Callable, Optional, Any, Union
from typing_extensions import Annotated

from telethon import events
from fastapi import Header, HTTPException, Depends
from fastapi.security import APIKeyQuery

from ..handlers import handlers, exceptions
from ..models.base_models import *
from ..database.base_repo import BaseRepository
from ..database.beanie_repo import BeanieRepository


# TODO: We need to get the instance not a class here
def get_repo() -> BaseRepository:
    return BeanieRepository()


api_key_query = APIKeyQuery(name="token", auto_error=False)
# q: what does this do?
# a


async def _verify_user(id: Union[Annotated[int, Header()], int]):
    verified = await handlers.check_user(id, get_repo())
    
    if not verified:
        raise exceptions.VerificationException("You are not a registered user. Please register and try again.")
    
async def _verify_admin(id: Union[Annotated[int, Header()], int]):
    verified = await handlers.is_admin(id, get_repo())
    
    if not verified:
        raise exceptions.VerificationException("YOu do not have the necessary admin rights to use this option.")
    

# NOTE: removed implementation for dependancy injected decorator (not needed due to dependency injection)
def verify_user(func: Callable) -> Callable:
    @wraps(func)
    async def wrapper(*args, **kwargs):  # id: Annotated[Optional[int], Header()] = None,
        if args and isinstance(args[1], events.common.EventCommon):
            event = args[1]
            sender_id = event.sender_id
        else:
            raise ValueError("The first argument must be a Telethon event.")
    
        await _verify_user(sender_id)
        
        return await func(*args, **kwargs)
    return wrapper

def verify_admin(func: Callable) -> Callable:
    @wraps(func)
    async def wrapper(*args, **kwargs):  # id: Annotated[Optional[int], Header()] = None,
        if args and isinstance(args[1], events.common.EventCommon):
            event = args[1]
            sender_id = event.sender_id
        else:
            raise ValueError("The first argument must be a Telethon event.")
    
        await _verify_admin(sender_id)
        
        return await func(*args, **kwargs)
    return wrapper


    
### TODO: Actually verify a meaningfull token
    
async def verify_token(token: Annotated[str, Header()]):
    if token != "token123":
        raise exceptions.VerificationException("Token header invalid")