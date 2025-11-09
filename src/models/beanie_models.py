from typing import Annotated, Optional, List, Dict, TYPE_CHECKING
import uuid

from beanie import Document, Indexed, Link, before_event, Insert, Replace, Save
from pymongo import IndexModel, ASCENDING
from pydantic import BaseModel, Field, field_validator

from datetime import datetime

from . import base_models as base 
from ..common.log import Logger
from ..common.helpers import hash_password, compare_password, is_valid_hash
from ..handlers.paypal import create_paypal_link, validate_paypal_link

logger = Logger("BeanieModels")

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
    # Stable identifier that persists across user type conversions
    stable_id: Annotated[str, Indexed(unique=True)] = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Stable UUID that persists when PassiveUser converts to TelegramUser"
    )
    
    first_name: str
    last_name: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    
    class Settings:
        is_root = False  # Don't use shared collection
        name = "base_users"  # This collection won't be used directly


class PassiveUser(base.PassiveUser, BaseUser):
    display_name: Annotated[str, Indexed(unique=True)]  # Required unique display name
    inactive_card_count: int = Field(default=0, ge=0, description="Number of consecutive coffee cards user was inactive for")
    is_archived: bool = Field(default=False, description="Whether user is archived due to inactivity (2-9 inactive cards)")
    is_disabled: bool = Field(default=False, description="Whether user is disabled due to long inactivity (10+ inactive cards)")
    
    class Settings(BaseUser.Settings):
        name = "passive_users"
        is_root = False


class TelegramUser(base.TelegramUser, PassiveUser):
    user_id: Annotated[int, Indexed(unique=True)]
    username: Annotated[str, Indexed(unique=True)]
    last_login: datetime
    phone: Optional[str] = None
    photo_id: Optional[int] = None
    paypal_link: Optional[str] = Field(None, description="PayPal payment link for coffee card purchases")
        
    @field_validator('paypal_link', mode='before')
    @classmethod
    def normalize_paypal_link(cls, v: Optional[str]) -> Optional[str]:
        """
        Normalize PayPal link input without performing network validation.
        Network validation is enforced on save via Beanie lifecycle hooks.
        """
        if not v:
            return None
            
        v = v.strip()
        if not v:
            return None
        
        # Only format/normalize here (no HTTP calls during model parsing/loading)
        formatted_link,_ = create_paypal_link(v)
        return formatted_link or None

    # Enforce PayPal link validation only when persisting changes
    @before_event(Insert)
    async def _validate_paypal_on_insert(self):
        if self.paypal_link:
            formatted, username = create_paypal_link(self.paypal_link)
            if not await validate_paypal_link(formatted, username):
                raise ValueError(f"PayPal link is not valid or doesn't exist: {formatted}")
            # ensure normalized form is stored
            self.paypal_link = formatted

    @before_event(Replace)
    @before_event(Save)
    async def _validate_paypal_on_save(self):
        if self.paypal_link:
            formatted, username = create_paypal_link(self.paypal_link)
            if not await validate_paypal_link(formatted, username):
                raise ValueError(f"PayPal link is not valid or doesn't exist: {formatted}")
            self.paypal_link = formatted
    
    class Settings(PassiveUser.Settings):
        name = "telegram_users"
        use_cache = False  # Disable caching to avoid stale data
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
        
        
#---------------------------
# *      Settings Sections
#---------------------------

# TODO: change default level for production
class LoggingSettings(BaseModel):
    """Logging configuration section."""
    level: str = Field(default="TRACE", description="Log level: TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL")
    show_time: bool = Field(default=False, description="Whether to show timestamp in logs")
    show_caller: bool = Field(default=False, description="Whether to show caller context [filename:function] in logs")
    show_class: bool = Field(default=True, description="Whether to show class name tags [ClassName] in logs")
    
    @field_validator('level')
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = ['TRACE', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        v_upper = v.upper()
        if v_upper not in valid_levels:
            raise ValueError(f"level must be one of {valid_levels}")
        return v_upper


class NotificationSettings(BaseModel):
    """Notification configuration section."""
    enabled: bool = Field(default=True, description="Whether notifications to other users are enabled globally")
    silent: bool = Field(default=False, description="Whether notifications should be sent silently by default (overrides user preference if True)")


class AppSettings(Document):
    """
    Global application settings organized into sections.
    This is a singleton document - there should only be one instance.
    """
    # Settings sections
    logging: LoggingSettings = Field(default_factory=LoggingSettings, description="Logging configuration")
    notifications: NotificationSettings = Field(default_factory=NotificationSettings, description="Notification configuration")
    
    class Settings:
        name = "app_settings"


class Config(base.Config, Document):
    password: Link[Password]
    admins: List[int]
    
    async def get_password(self) -> Optional[Password]:
        # Instead of using Link, query Password collection directly
        try:
            # Query the first (and should be only) password document
            password = await Password.find_one()
            if password:
                return password
            else:
                logger.warning("No password document found in database")
                return None
        except Exception as e:
            logger.error(f"Error fetching password: {e}", exc_info=e)
            return None
        
    class Settings:
        name = "config"
        
class UserSettings(Document):
    """Stores user-specific settings for a Telegram user."""
    user_id: Annotated[int, Indexed(unique=True)] = Field(..., description="Telegram user ID")
    
    # Group keyboard settings
    group_page_size: int = Field(default=10, ge=5, le=20, description="Number of users displayed per group selection page (5-20)")
    group_sort_by: str = Field(default="alphabetical", description="How to sort active users: 'alphabetical' or 'coffee_count'")
    
    # Vanishing messages settings
    vanishing_enabled: bool = Field(default=True, description="Whether vanishing messages are enabled")
    vanishing_threshold: int = Field(default=2, ge=1, le=10, description="Number of messages/conversations before message vanishes (1-10)")
    
    # Notification settings (user preference)
    notifications_silent: bool = Field(default=False, description="Whether notifications should be sent silently (user preference, can be overridden by app settings)")
    
    @field_validator('group_sort_by')
    @classmethod
    def validate_sort_by(cls, v: str) -> str:
        if v not in ['alphabetical', 'coffee_count']:
            raise ValueError("group_sort_by must be 'alphabetical' or 'coffee_count'")
        return v
    
    class Settings:
        name = "user_settings"
        use_cache = False  # Disable caching for user settings
        is_root = False  # Don't use shared collection