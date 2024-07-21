from typing import Annotated, Optional, List

from beanie import Document, Indexed, Link
from pydantic import Field, model_validator
from pydantic.functional_validators import field_validator

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


#---------------------------
# *      Users
#---------------------------

class BaseUser(base.BaseUser, Document):
    first_name: str
    last_name:  Optional[str] = None
    created_at: datetime      = Field(default_factory = datetime.now)
    updated_at: datetime      = Field(default_factory = datetime.now)
    
    class Settings:
        is_root = True
    
    
class TelegramUser(base.TelegramUser, BaseUser):
    id:         int # Indexed(int, unique = True)  is not allowed     
    username:   Indexed(int, unique = True) #Annotated[int, Indexed(unique = True)]
    last_login: datetime
    phone:      Optional[Indexed(str, unique = True)] = None
    photo_id:   int
    lang_code:  str = "en"
        
    class Settings:
        name = "telegram_users"
        use_cache = True

        
class FullUser(base.FullUser, TelegramUser):
    gsheet_name: Indexed(str, unique=True)
    is_admin:    bool = False
    
    class Settings:
        name = "full_users"
    
#---------------------------
# *      Configuration
#---------------------------
    
class Password(base.Password, Document):
    hash_value: bytes
    
    @field_validator("hash_value", mode="before")
    @classmethod
    def set_password(cls, password: str | bytes) -> bytes:
        # print("#### Password - set_password - called")
        if isinstance(password, bytes):
            # print("Password - set_password - obtained type bytes:", password)
            try:
                password = password.decode("utf8")
            except UnicodeDecodeError as e:
                raise ValueError("Password in database is no valid utf8-byte string")
            return password
        
        elif not isinstance(password, str):
            raise ValueError("Password must either be a utf8-byte string hash or a plaintext string.")
        
        # print("Password - set_password - obtained type str:", password)
        return hash_password(password)
    
    def verify_password(self, plain_password: str) -> bool:
        return compare_password(plain_password, self.hash_value)
        
        
class Config(base.Config, Document):
    password: Link[Password]
    admins:   List[int] #EmbeddedDocumentField(document_type = FullUserDocument)
    
    def get_password(self) -> Link[Password]:
        print("Config - get_password")
        return self.password 
        #alternative to fetch_links=true in find_one:
        # return config.fetch_link(Config.password)
        
    class Settings:
        name = "config"
        