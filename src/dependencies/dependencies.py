from functools import wraps
from typing import Callable, Union, TYPE_CHECKING
from typing_extensions import Annotated

from fastapi import Header

from ..handlers import handlers, exceptions
from ..database.beanie_repo import BeanieRepository
from ..database.base_repo import BaseRepository




def get_repo() -> "BaseRepository":
    return BeanieRepository()

def repo(func: Callable) -> Callable:
    @wraps(func)
    async def wrapper(*args, **kwargs):
        repository = get_repo()
        
        if args and isinstance(args[0], BaseRepository):
            return await func(*args, **kwargs)
        else:
            return await func(repository, *args, **kwargs)
    
    return wrapper

# api_key_query = APIKeyQuery(name="token", auto_error=False)
# q: what does this do?
# a


async def _verify_user(id: Union[Annotated[int, Header()], int]):
    verified = await handlers.check_user(id)
    
    if not verified:
        raise exceptions.VerificationException("❌ You are not a registered user. Please register using /start and try again.")
    
async def _verify_admin(id: Union[Annotated[int, Header()], int]):
    verified = await handlers.is_admin(id)
    
    if not verified:
        raise exceptions.VerificationException("❌ You do not have the necessary admin rights to use this option.")
    

# NOTE: removed implementation for dependancy injected decorator (not needed due to dependency injection)
def verify_user(func: Callable) -> Callable:
    @wraps(func)
    async def wrapper(*args, **kwargs):  # id: Annotated[Optional[int], Header()] = None,
        if args and hasattr(args[1], 'sender_id'):
            event = args[1]
            sender_id = event.sender_id
        else:
            raise ValueError("The first argument must be a Telethon event with sender_id.")
    
        await _verify_user(sender_id)
        
        return await func(*args, **kwargs)
    return wrapper

def verify_admin(func: Callable) -> Callable:
    @wraps(func)
    async def wrapper(*args, **kwargs):  # id: Annotated[Optional[int], Header()] = None,
        if args and hasattr(args[1], 'sender_id'):
            event = args[1]
            sender_id = event.sender_id
        else:
            raise ValueError("The first argument must be a Telethon event with sender_id.")
    
        await _verify_admin(sender_id)
        
        return await func(*args, **kwargs)
    return wrapper


    
### TODO: Actually verify a meaningfull token
    
async def verify_token(token: Annotated[str, Header()]):
    if token != "token123":
        raise exceptions.VerificationException("Token header invalid")