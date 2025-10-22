from abc import ABC, abstractmethod
from typing import Optional, List
from datetime import datetime

#---------------------------
# *      Users
#---------------------------

class BaseUser(ABC):
    first_name: str
    last_name:  Optional[str]      # can be optional
    created_at: datetime # should be defined at creation
    updated_at: datetime # should be defined at creation and update
    
    # should be the root model
    
    
class PassiveUser(BaseUser, ABC):
    display_name: str  # Required unique display name for passive users
    inactive_card_count: int  # Track consecutive cards without orders
    is_archived: bool  # Whether user is archived due to inactivity (2-9 cards)
    is_disabled: bool  # Whether user is disabled due to long inactivity (10+ cards)
    
    # should be in passive_users collection
    # These users don't have Telegram accounts but can have coffee orders/debts managed by admins


class TelegramUser(PassiveUser, ABC):
    user_id:     int # should be unique
    username:    str # should be unique 
    last_login:  datetime
    phone:       Optional[str] # optional and unique
    photo_id:    Optional[int]
    lang_code:   str = "en"
    paypal_link: Optional[str]
    
    # should be in telegram_users collection
    # can use cache if available
    
#---------------------------
# *      Configuration
#---------------------------
    
class Password(ABC):
    hash_value: str
    
    @classmethod
    @abstractmethod
    def set_password(cls, password: str | bytes) -> str:
        pass
    
    @abstractmethod
    def verify_password(self, plain_password: str) -> bool:
        pass
        
    
class Config(ABC):
    # password should be a link to Password document in Beanie implementation
    # admins should be a list of integers (user ids)
    admins: List[int]
    
    @abstractmethod
    async def get_password(self) -> Optional["Password"]:
        """Get the linked password. Implementation depends on the specific ORM/ODM."""
        pass
    
    # should be in config collection
    
#---------------------------
# *      Application Settings
#---------------------------

class LoggingSettings(ABC):
    """Logging configuration section for application settings."""
    level: str          # TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL
    show_time: bool     # Whether to show timestamp in logs
    show_caller: bool   # Whether to show caller context [filename:function]
    show_class: bool    # Whether to show class name tags [ClassName]


class AppSettings(ABC):
    """Global application settings organized into sections (singleton)."""
    logging: LoggingSettings

        