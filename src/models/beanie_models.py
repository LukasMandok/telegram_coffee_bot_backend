from typing import Annotated, Optional, List, Dict, TYPE_CHECKING, Any
import uuid

from beanie import Document, Indexed, Link, before_event, Insert, Replace, Save
from pymongo import IndexModel, ASCENDING
from pydantic import BaseModel, Field, field_validator, ConfigDict
from bson import ObjectId

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
        
        
class SnapshotCollectionChunkInfo(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    chunk_ids: List[ObjectId] = Field(default_factory=list)
    document_count: int = Field(default=0, ge=0)


class SnapshotMeta(Document):
    """Snapshot metadata.

    Stored separately from snapshot data chunks (see `snapshots_data` collection).
    """

    snapshot_number: Annotated[int, Indexed(unique=True)]
    snapshot_id: Annotated[str, Indexed(unique=True)]

    created_at: datetime = Field(default_factory=datetime.now)
    committed_at: Optional[datetime] = None

    reasons: List[str] = Field(default_factory=list)
    contexts: List[str] = Field(default_factory=list)

    status: str = Field(default="committed")

    # Permanent snapshots are excluded from retention pruning (keep_last).
    permanent: bool = Field(default=False)

    # Full snapshots are intended as navigation anchors (jump points) for restores.
    # Weekly snapshots and manual snapshots should be marked as full.
    full_snapshot: bool = Field(default=False)

    collections: Dict[str, SnapshotCollectionChunkInfo] = Field(default_factory=dict)
    total_documents: int = Field(default=0, ge=0)

    loaded_at: Optional[datetime] = None
    loaded_by_user_id: Optional[int] = None

    obsolete: bool = Field(default=False)
    obsoleted_at: Optional[datetime] = None
    obsoleted_by_snapshot_number: Optional[int] = None

    pre_restore_for_snapshot_number: Optional[int] = None

    class Settings:
        name = "snapshots_meta"
        use_cache = False


class SnapshotHistory(Document):
    """Tracks snapshot numbers used for the history-based restore algorithm."""

    key: Annotated[str, Indexed(unique=True)] = Field(default="default")
    snapshot_numbers: List[int] = Field(default_factory=list)
    last_snapshot_number: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=datetime.now)

    class Settings:
        name = "snapshot_history"
        use_cache = False


class SnapshotDataChunk(Document):
    """Chunked snapshot payload documents.

    This backs the `snapshots_data` collection and is referenced by `SnapshotMeta.collections[*].chunk_ids`.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    snapshot_id: Annotated[str, Indexed()]
    source_collection: Annotated[str, Indexed()]
    chunk_index: int

    created_at: datetime = Field(default_factory=datetime.now)
    document_count: int = Field(default=0, ge=0)
    documents: List[Dict[str, Any]] = Field(default_factory=list)

    class Settings:
        name = "snapshots_data"
        use_cache = False


class Config(base.Config, Document):
    password: Link[Password]
    admins: List[int]
    owner_user_id: Optional[int] = None
    
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