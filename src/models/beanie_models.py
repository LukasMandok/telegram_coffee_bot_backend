from typing import Annotated, Optional, List, TYPE_CHECKING

from beanie import Document, Indexed, Link
from pydantic import Field, field_validator

from datetime import datetime

from . import base_models as base 
from ..common.helpers import hash_password, compare_password

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
        is_root = True


class TelegramUser(base.TelegramUser, BaseUser):
    user_id: Annotated[int, Indexed(unique=True)]
    username: Annotated[str, Indexed(unique=True)]
    last_login: datetime
    phone: Annotated[Optional[str], Indexed(unique=True)] = None
    photo_id: Optional[int] = None
        
    class Settings(BaseUser.Settings):
        name = "telegram_users"
        use_cache = True
        is_root = False

        
class FullUser(base.FullUser, TelegramUser):
    gsheet_name: Annotated[str, Indexed(unique=True)]
    
    class Settings(TelegramUser.Settings):
        name = "full_users"
        is_root = False
    
#---------------------------
# *      Configuration
#---------------------------
    
class Password(base.Password, Document):
    hash_value: str
    
    @field_validator("hash_value", mode="before")
    @classmethod
    def set_password(cls, password: str | bytes) -> str:
        # print("#### Password - set_password - called")
        if isinstance(password, bytes):
            # print("Password - set_password - obtained type bytes:", password)
            try:
                password = password.decode("utf8")
            except UnicodeDecodeError as e:
                raise ValueError("Password in database is no valid utf8-byte string")
        
        elif not isinstance(password, str):
            raise ValueError("Password must either be a utf8-byte string hash or a plaintext string.")
        
        # print("Password - set_password - obtained type str:", password)
        # Hash the password and return as string for storage
        hashed_bytes = hash_password(password)
        return hashed_bytes.decode('utf-8')
    
    def verify_password(self, plain_password: str) -> bool:
        # Convert string hash back to bytes for comparison
        hash_bytes = self.hash_value.encode('utf-8')
        return compare_password(plain_password, hash_bytes)
        
        
class Config(base.Config, Document):
    password: Link[Password]
    admins: List[int]
    
    async def get_password(self) -> Optional[Password]:
        print("Config - get_password")
        # Use fetch_link to resolve the linked document
        try:
            # Fetch the linked password document using the correct syntax
            await self.fetch_link(Config.password)
            password = self.password
            print(f"Config - get_password - fetched password: {password}")
            print(f"Config - get_password - password hash_value: {getattr(password, 'hash_value', 'NO_HASH_VALUE')}")
            # Type cast to handle the Link[Password] -> Password conversion
            if hasattr(password, 'hash_value'):
                return password  # type: ignore
            return None
        except Exception as e:
            print(f"Error fetching password link: {e}")
            return None
        
    class Settings:
        name = "config"
        