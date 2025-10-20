from typing import Annotated, Optional, List, Dict, TYPE_CHECKING
import uuid

from beanie import Document, Indexed, Link, before_event, Insert
from pymongo import IndexModel, ASCENDING
from pydantic import Field, field_validator

from datetime import datetime

from . import base_models as base 
from ..common.helpers import hash_password, compare_password, is_valid_hash
from ..handlers.paypal import create_paypal_link, validate_paypal_link

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
    def validate_and_format_paypal_link(cls, v: Optional[str]) -> Optional[str]:
        """
        Validate and format PayPal link automatically.
        
        This validator:
        1. Formats various PayPal input formats into proper paypal.me links
        2. Validates that the PayPal link actually exists
        3. Makes PayPal handling consistent across the application
        """
        if not v:
            return None
            
        v = v.strip()
        if not v:
            return None
        
        # Format the PayPal link using the existing logic
        formatted_link = create_paypal_link(v)
        
        # Validate the link exists (synchronous validation)
        is_valid = validate_paypal_link(formatted_link)
        if not is_valid:
            raise ValueError(f"PayPal link is not valid or doesn't exist: {formatted_link}")
        
        return formatted_link
    
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
        
        
class Config(base.Config, Document):
    password: Link[Password]
    admins: List[int]
    
    user_settings: Dict[int, Link["UserSettings"]] = Field(default_factory=dict, description="List of user settings links")
    
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
        
class UserSettings(Document):
    """Stores user-specific settings."""
    
    class Settings:
        name = "user_settings"
        use_cache = False  # Disable caching for user settings
        is_root = False  # Don't use shared collection