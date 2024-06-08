from abc import ABC, abstractmethod
from typing import Annotated, Optional, List
from datetime import datetime

#---------------------------
# *      Users
#---------------------------

class BaseUser(ABC):
    first_name: str
    last_name:  Optional[str]      # can be optional
    created_at: datetime # should be defined at creation
    updated_at: datetime # should be defined at creation and update
    
    # should be the rout model
    
    
class TelegramUser(BaseUser, ABC):
    id:         int # should be unique
    username:   int # should be unique 
    last_login: datetime
    phone:      Optional[str] # optional and unique
    photo_id:   Optional[int]
    lang_code:  str = "en"
    
    # should be in telegram_users collection
    # can use cache if available
        

class FullUser(TelegramUser, ABC):
    gsheet_name: str # should be unique
    is_admin:    bool = False
    
    # should be in full_users collection
    
#---------------------------
# *      Configuration
#---------------------------
    
class Password(ABC):
    hash_value: str
    
    @classmethod
    @abstractmethod
    def set_password(cls, password: str) -> str:
        pass
    
    @abstractmethod
    def verify_password(self, password: str) -> bool:
        pass
        
    
class Config(ABC):
    password: Password  # should be a link
    admins:   List[int]         # should be a list of integers (user ids)
    
    # should be in config collection
        