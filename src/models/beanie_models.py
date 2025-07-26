from typing import Annotated, Optional, List, TYPE_CHECKING

from beanie import Document, Indexed, Link
from pydantic import Field, field_validator

from datetime import datetime

from . import base_models as base 
from ..common.helpers import hash_password, compare_password, is_valid_hash

"""
### IDEAS

- use Projections (.project) to project DOcument onto a simpler Version, which contains everything necessary during querys
- you can use .sort on queries to sort the documents based on an value
- one can stack find queries after each other (same as multiple requirements separated by comma in on find)
- Document.find_all() == Document.find({})

- Managing Documents with different schemas -> Mutli-model pattern (stored in single collection)
    -> used to filter out certain documents, when querying  
    
- Settings: is_root -> will lead to all inherited documents be saved in the same collection
- Multiple inheritance (mixin) is possible
- queries wirh (with_children = True) will contain also child classes
- Linked documents will resolve in repective classes

- StateManagment:
- allows to keep changes before inserting them into the database:
- Settings: use_state_managment = True
- -> allows to view and rollback changes before saving them to the database
- also possible for previous changes made in the database
"""

# TODO: use pydantic reduces models as projections to save bandwidth, when only requiereing specific parts of a document 
''' example:
class ProductShortView(BaseModel):
    name: str
    price: float

chocolates = await Product.find(
    Product.category.name == "Chocolate").project(ProductShortView).to_list()
'''


#---------------------------
# *      Users
#---------------------------

class BaseUser(base.BaseUser, Document):
    first_name: str
    last_name: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    
    class Settings:
        is_root = False  # Don't use shared collection
        name = "base_users"  # This collection won't be used directly


class TelegramUser(base.TelegramUser, BaseUser):
    user_id: Annotated[int, Indexed(unique=True)]
    username: Annotated[str, Indexed(unique=True)]
    last_login: datetime
    phone: Annotated[Optional[str], Indexed(unique=True, sparse=True)] = None  # Unique but sparse to allow nulls
    photo_id: Optional[int] = None
        
    class Settings(BaseUser.Settings):
        name = "telegram_users"
        use_cache = False  # Disable caching to avoid stale data
        is_root = False

        
class FullUser(base.FullUser, TelegramUser):
    display_name: Annotated[str, Indexed(unique=True, sparse=True)]  # Required unique display name, sparse allows TelegramUser to have null
    
    class Settings(TelegramUser.Settings):
        name = "full_users"
        use_cache = False  # Disable caching to avoid stale data
        is_root = False


class PassiveUser(base.PassiveUser, BaseUser):
    display_name: Annotated[str, Indexed(unique=True)]  # Required unique display name
    
    class Settings(BaseUser.Settings):
        name = "passive_users"
        is_root = False
    
#-----------------------------
# *      Configuration
#-----------------------------
    
class Password(base.Password, Document):
    hash_value: str

    @field_validator("hash_value", mode="before")
    @classmethod
    def set_password(cls, password: str | bytes) -> str:
        if isinstance(password, str) and is_valid_hash(password):
            return password  # loaded from DB, already hashed
        if isinstance(password, (bytes, bytearray)):
            password = password.decode("utf-8")
        if isinstance(password, str):
            return hash_password(password).decode("utf-8")
        raise ValueError("Password must be bytes or str")
    
    def verify_password(self, plain_password: str) -> bool:
        # Convert string hash back to bytes for comparison
        hash_bytes = self.hash_value.encode('utf-8')
        return compare_password(plain_password, hash_bytes)
        
        
class Config(base.Config, Document):
    password: Link[Password]
    admins: List[int]
    
    async def get_password(self) -> Optional[Password]:
        print("Config - get_password")
        # Instead of using Link, query Password collection directly
        try:
            # Query the first (and should be only) password document
            password = await Password.find_one()
            print(f"Config - get_password - found password: {password}")
            if password:
                print(f"Config - get_password - password hash_value: {getattr(password, 'hash_value', 'NO_HASH_VALUE')}")
                return password
            else:
                print("Config - get_password - no password document found in database")
                return None
        except Exception as e:
            print(f"Error fetching password: {e}")
            return None
        
    class Settings:
        name = "config"
        