# from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import AsyncMongoClient
import logging
from beanie import init_beanie
from typing import Optional, Dict, Any
from datetime import datetime
import traceback

from .base_repo import BaseRepository, EffectiveNotificationPolicy
from ..models import beanie_models as models
from ..models.coffee_models import CoffeeCard, CoffeeOrder, Payment, UserDebt, CoffeeSession
from ..models.feedback_models import Feedback
from ..common.log import (
    Logger,
    log_settings,
)

from ..config import app_config
from ..common.helpers import hash_password
from .snapshot_manager import SnapshotManager


# Raised when an automatic display-name cannot be generated because an existing
# user already has the identical first+last name. The flow should catch this
# and ask the registering user to pick a custom display name.
class DisplayNameConflictError(Exception):
    def __init__(self, existing_display_name: str):
        super().__init__(f"Display name conflict with existing display name: {existing_display_name}")
        self.existing_display_name = existing_display_name


class BeanieRepository(BaseRepository):
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(BeanieRepository, cls).__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return

        self.uri = None
        self.db = None
        self.logger = Logger("BeanieRepository")
        self.client = None
        self.snapshot_manager: SnapshotManager | None = None
        self._initialized = True

    # ---------------------------
    #     Connection
    # ---------------------------

    async def connect(self, uri):
        self.uri = uri

        try:
            # await DataBase.connect(uri = self.uri, db = "fastapi")
            # self.client = AsyncIOMotorClient(self.uri)
            self.client = AsyncMongoClient(self.uri)
            self.db = self.client[app_config.MONGO_INITDB_DATABASE]

            await init_beanie(self.db, document_models=[
                models.BaseUser,
                models.TelegramUser,
                models.PassiveUser,
                models.Password,
                models.Config,
                models.AppSettings,
                models.UserSettings,
                models.SnapshotMeta,
                models.SnapshotHistory,
                models.SnapshotDataChunk,
                Feedback,
                # Coffee models
                CoffeeCard,
                CoffeeOrder,
                Payment,
                UserDebt,
                CoffeeSession])

            # IDEA: maybe use asyncio.create_task to run them in the background

            await self.ping()

            self.snapshot_manager = SnapshotManager(self.db)
            
        except Exception as e:
            self.logger.error("MongoDB connection failed", extra_tag="DB", exc=e)
            raise

    async def close(self):
        try:
            if self.client:
                await self.client.close()
            self.logger.info("Disconnected from MongoDB", extra_tag="DB")
        except Exception as e:
            self.logger.error("close_connection failed", extra_tag="DB", exc=e)

    async def setup_defaults(self):
        # Clear existing data to start fresh (temporary fix for development)
        self.logger.info("Setting up default configuration...")
        await models.Config.delete_all()
        await models.Password.delete_all()
        await models.AppSettings.delete_all()
        
        password_doc = models.Password(
            hash_value = app_config.DEFAULT_PASSWORD
        )
        
        # Create Config with Link to password
        new_config = models.Config(
            password = password_doc,  # type: ignore[arg-type]  # Beanie accepts Document instances for Link fields
            admins=[app_config.DEFAULT_ADMIN],
            owner_user_id=app_config.DEFAULT_ADMIN,
        )
        
        # Create default Settings
        app_settings = models.AppSettings()
        
        await password_doc.insert()
        await new_config.insert()
        await app_settings.insert()

        self.logger.info(
            f"Default database values set up (admins={[app_config.DEFAULT_ADMIN]})",
            extra_tag="DB",
        )
        self.logger.info("Default configuration and settings created successfully")

    # ---------------------------
    #     Helper Methods
    # ---------------------------

    async def ping(self):
        try:
            if self.client:
                await self.client.admin.command('ping')
            # Database ping success logged in connect method
        except Exception as e:
            self.logger.error("ping failed", extra_tag="DB", exc=e)


    async def get_collection(self, collection_name):
        if self.db is None:
            raise RuntimeError("Database not connected; call connect() first")
        return self.db.get_collection(collection_name)

    # ---------------------------
    #     Access Database
    # ---------------------------

    ### Users ###

    async def find_all_users(self, exclude_archived: bool = False, exclude_disabled: bool = False):
        """
        Find all users (both TelegramUser and PassiveUser).
        
        Args:
            exclude_archived: If True, exclude users with is_archived=True
            exclude_disabled: If True, exclude users with is_disabled=True
            
        Returns:
            List of all users matching the criteria
        """
        try:
            # Get telegram users using the dedicated method
            telegram_users = await self.find_all_telegram_users(
                exclude_archived=exclude_archived,
                exclude_disabled=exclude_disabled
            )
            
            # Build query for passive users
            passive_query = {}
            if exclude_archived:
                passive_query['is_archived'] = False
            if exclude_disabled:
                passive_query['is_disabled'] = False
            
            # Get passive users with filters
            if passive_query:
                passive_users = await models.PassiveUser.find(passive_query).to_list()
            else:
                passive_users = await models.PassiveUser.find_all().to_list()
            
            all_users = telegram_users + passive_users
            self.logger.trace(f"find_all_users ({len(all_users)} records)", extra_tag="PERF")
            return all_users

        except Exception as e:
            self.logger.error("find_all_users failed", extra_tag="DB", exc=e)
            return []
    
    async def find_all_telegram_users(self, exclude_archived: bool = False, exclude_disabled: bool = False):
        """
        Find all Telegram users (users with user_id).
        
        Args:
            exclude_archived: If True, exclude users with is_archived=True
            exclude_disabled: If True, exclude users with is_disabled=True
            
        Returns:
            List of TelegramUser objects matching the criteria
        """
        try:
            # Build query
            query = {}
            if exclude_archived:
                query['is_archived'] = False
            if exclude_disabled:
                query['is_disabled'] = False
            
            # Get telegram users with filters
            if query:
                telegram_users = await models.TelegramUser.find(query).to_list()
            else:
                telegram_users = await models.TelegramUser.find_all().to_list()

            self.logger.trace(f"find_all_telegram_users ({len(telegram_users)} records)", extra_tag="PERF")
            return telegram_users

        except Exception as e:
            self.logger.error("find_all_telegram_users failed", extra_tag="DB", exc=e)
            return []
        

    async def find_user_by_id(self, id: int):
        # Telegram users are the only ones with a user_id
        telegram_user = await models.TelegramUser.find_one(models.TelegramUser.user_id == id)
        if telegram_user:
            self.logger.debug(f"Found user: {telegram_user.display_name} (id={id})")
        else:
            self.logger.debug(f"No user found with id={id}")
        
        return telegram_user
    
    async def find_user_by_display_name(self, display_name: str):
        """Find a user (TelegramUser or PassiveUser) by display name."""
        # Check TelegramUser first
        telegram_user = await models.TelegramUser.find_one(models.TelegramUser.display_name == display_name)
        if telegram_user:
            return telegram_user

        # Check PassiveUser
        passive_user = await models.PassiveUser.find_one(models.PassiveUser.display_name == display_name)
        return passive_user
    
    async def find_user_by_id_string(self, id_string: str):
        """Find a user (TelegramUser or PassiveUser) by their ObjectId string."""
        from bson import ObjectId
        
        try:
            object_id = ObjectId(id_string)
        except:
            return None
        
        # Check TelegramUser first
        telegram_user = await models.TelegramUser.get(object_id)
        if telegram_user:
            return telegram_user
        
        # Check PassiveUser
        passive_user = await models.PassiveUser.get(object_id)
        return passive_user

    async def is_user_admin(self, user_id: int) -> bool:
        """Check if a user is an admin by looking up their user_id in config."""
        try:
            admins = await self.get_admins()
            return user_id in admins if admins else False
        except Exception as e:
            self.logger.error(f"is_user_admin failed (user_id={user_id})", extra_tag="DB", exc=e)
            return False
    
    async def create_telegram_user(
        self,
        user_id: int,
        username: str,
        first_name: str,
        last_name: Optional[str] = None,
        phone: Optional[str] = None,
        photo_id: Optional[int] = None,
        lang_code: str = "en",
        paypal_link: Optional[str] = None,
        display_name: Optional[str] = None,
    ):
        """Create a new TelegramUser in the database."""
        try:
            # Check if user already exists
            existing_user = await self.find_user_by_id(user_id)
            if existing_user:
                self.logger.warning(f"create_telegram_user skipped (user_id={user_id} already exists)", extra_tag="DB")
                return existing_user
            # Use provided display_name if given, otherwise generate a unique one
            if display_name is None:
                display_name = await self._generate_unique_display_name(first_name, last_name)
            self.logger.debug(f"Creating user: {display_name} (id={user_id})")
            
            new_user = models.TelegramUser(
                user_id=user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                phone=phone,  # Now it's safe to include None values
                photo_id=photo_id,
                display_name=display_name,
                paypal_link=paypal_link,
                last_login=datetime.now(),
                created_at=datetime.now(),
                updated_at=datetime.now()
            )
            
            # Set lang_code after creation since it might be inherited
            new_user.lang_code = lang_code
            
            await new_user.insert()
            self.logger.info(
                f"User registered (user_id={user_id}, username={username})",
                extra_tag="USER",
            )
            return new_user
            
        except Exception as e:
            self.logger.error(
                f"create_telegram_user failed (user_id={user_id}, username={username})",
                extra_tag="DB",
                exc=e,
            )
            raise

    # TODO: implement this for multiple users with the same forname to check all last names against each other
    async def _generate_unique_display_name(self, first_name: str, last_name: Optional[str] = None) -> str:
        """
        Generate a unique display name by progressively adding characters from last name.
        Works for both TelegramUser and PassiveUser types.
        
        Logic:
        1. Try just first_name
        2. If conflict, compare last names and add minimum letters needed to differentiate both users
        
        Args:
            first_name: User's first name
            last_name: User's last name (optional)
            
        Returns:
            str: Unique display name
        """
        candidate_name = first_name

        # If a user with the exact same first+last name already exists (either
        # PassiveUser or TelegramUser), treat this as a conflict that requires
        # manual display-name selection by the registering user.
        if first_name and (last_name is not None):
            existing_same_telegram = await models.TelegramUser.find_one(
                models.TelegramUser.first_name == first_name,
                models.TelegramUser.last_name == last_name,
            )
            existing_same_passive = await models.PassiveUser.find_one(
                models.PassiveUser.first_name == first_name,
                models.PassiveUser.last_name == last_name,
            )
            existing_same = existing_same_telegram or existing_same_passive
            if existing_same:
                raise DisplayNameConflictError(existing_same.display_name)

        # Check for conflicts in both TelegramUser and PassiveUser collections
        existing_telegram_user = await models.TelegramUser.find_one(models.TelegramUser.display_name == candidate_name)
        existing_passive_user = await models.PassiveUser.find_one(models.PassiveUser.display_name == candidate_name)

        existing_user = existing_telegram_user or existing_passive_user

        if not existing_user:
            return candidate_name

        if not last_name:
            return f"{first_name}_{hash(first_name) % 1000}"
        
        # Name conflict detected - need to differentiate both users
        if not existing_user.last_name:
            # Existing user has no last name, just add one letter to new user
            return f"{first_name} {last_name[0]}."
        
        # Both users have last names - find minimum letters needed to differentiate
        existing_last_name = existing_user.last_name
        min_length = min(len(last_name), len(existing_last_name))
        
        # Find the first differing character position
        letters_needed = 1
        for i in range(min_length):
            if last_name[i].lower() != existing_last_name[i].lower():
                letters_needed = i + 1
                break
        else:
            # One last name is a prefix of the other, need full shorter name + 1
            letters_needed = min_length + 1
        
        # Ensure we don't exceed the length of either last name
        letters_needed = min(letters_needed, len(last_name), len(existing_last_name))
        
        # Update existing user's display name
        new_existing_display_name = f"{existing_user.first_name} {existing_last_name[:letters_needed]}."
        await existing_user.set({"display_name" : new_existing_display_name, 
                                 "updated_at": datetime.now()})
        # existing_user.display_name = new_existing_display_name
        # existing_user.updated_at = datetime.now()
        # await existing_user.save()
        
        self.logger.info(f"Updated existing user's display name from '{first_name}' to '{new_existing_display_name}'")
        
        # Generate new user's display name
        new_user_display_name = f"{first_name} {last_name[:letters_needed]}."
        return new_user_display_name

    async def create_passive_user(self, first_name: str, last_name: Optional[str] = None) -> models.PassiveUser:
        """Create a new PassiveUser with smart display name generation."""
        try:
            # First check if a TelegramUser with the same name already exists
            existing_telegram_user = await models.TelegramUser.find_one(
                models.TelegramUser.first_name == first_name,
                models.TelegramUser.last_name == last_name
            )
            
            if existing_telegram_user:
                self.logger.info(f"Cannot create passive user - Telegram user '{existing_telegram_user.display_name}' already exists")
                raise ValueError(
                    f"Cannot create passive user: A Telegram user with the name '{first_name} {last_name or ''}' already exists. "
                    f"Display name: {existing_telegram_user.display_name}"
                )
            
            # Generate unique display name
            display_name = await self._generate_unique_display_name(first_name, last_name)
            
            new_user = models.PassiveUser(
                first_name=first_name,
                last_name=last_name,
                display_name=display_name,
                created_at=datetime.now(),
                updated_at=datetime.now()
            )
            
            await new_user.insert()
            self.logger.info(f"Created passive user: {display_name}")
            return new_user
            
        except ValueError as e:
            # This is expected when user already exists - don't log as error
            raise
        except Exception as e:
            self.logger.error(
                f"create_passive_user failed (first_name={first_name}, last_name={last_name})",
                extra_tag="DB",
                exc=e,
            )
            raise

    async def find_passive_user_by_name(self, first_name: str, last_name: Optional[str] = None) -> models.PassiveUser | None:
        """Find a passive user by first name and last name."""
        try:
            # Simple approach: find by first_name and last_name directly
            result = await models.PassiveUser.find(
                models.PassiveUser.first_name == first_name,
                models.PassiveUser.last_name == last_name
            ).first_or_none()
            
            return result
        except Exception as e:
            self.logger.error(
                f"find_passive_user_by_name failed (first_name={first_name}, last_name={last_name})",
                extra_tag="DB",
                exc=e,
            )
            return None

    # TODO: this has to ckecked later, that the depbts and all other information correctly transfer.
    async def convert_passive_to_telegram_user(
        self,
        passive_user,
        user_id: int,
        username: str,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        phone: Optional[str] = None,
        photo_id: Optional[int] = None,
        lang_code: str = "en",
        paypal_link: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> models.TelegramUser:
        """Convert a PassiveUser to a TelegramUser by transferring data and deleting the passive user."""
        try:
            # Use provided names or fall back to passive user's names
            final_first_name = first_name if first_name is not None else passive_user.first_name
            final_last_name = last_name if last_name is not None else passive_user.last_name
            # Use provided display_name if present, otherwise use the passive user's display name
            if display_name is None:
                display_name = passive_user.display_name
            self.logger.info(f"Converting passive user '{display_name}' to Telegram user (id={user_id})")
            
            # Create new TelegramUser with data from PassiveUser
            new_telegram_user = models.TelegramUser(
                user_id=user_id,
                username=username,
                first_name=final_first_name,
                last_name=final_last_name,
                phone=phone,
                photo_id=photo_id,
                display_name=display_name,  # Use the existing display name from passive user
                paypal_link=paypal_link,
                last_login=datetime.now(),
                created_at=passive_user.created_at,  # Preserve original creation date
                updated_at=datetime.now(),
                stable_id=passive_user.stable_id  # Preserve stable_id across conversion
            )
            
            # Set lang_code after creation
            new_telegram_user.lang_code = lang_code
            
            # Insert new telegram user
            await new_telegram_user.insert()
            
            # Delete the passive user
            await passive_user.delete()

            self.logger.info(
                f"User registered (user_id={user_id}, username={username})",
                extra_tag="USER",
            )
            return new_telegram_user
            
        except Exception as e:
            self.logger.error(
                f"convert_passive_to_telegram_user failed (user_id={user_id}, passive_user_id={passive_user.id})",
                extra_tag="DB",
                exc=e,
            )
            raise

    ### Configuration ###

    async def get_password(self):
        config = await models.Config.find_one()
        if config:
            password = await config.get_password()
            return password
        else:
            self.logger.warning("No config found when fetching password")
            return None

    async def update_password(self, new_password: str) -> bool:
        """Update or create the registration Password document and link it to Config.

        Creates a new Password document with the provided value (the model's
        validator will hash it) and updates the Config link. Removes the old
        password document if present.
        """
        try:
            # Create new password document (validator hashes plain strings)
            new_pw = models.Password(hash_value=new_password)
            await new_pw.insert()

            # Attach to config
            config = await models.Config.find_one()
            if config:
                # Link new password and save
                config.password = new_pw
                await config.save()

            # Remove old password documents except the newly created one
            try:
                old = await models.Password.find_one({'_id': {'$ne': new_pw.id}})
                if old:
                    await old.delete()
            except Exception:
                # Non-fatal: keep the new password even if cleanup fails
                pass

            self.logger.info("Updated registration password", extra_tag="AUTH")
            return True
        except Exception as e:
            self.logger.error(f"update_password failed: {e}", extra_tag="DB", exc=e)
            return False

    async def get_admins(self):
        """Get the list of admin user IDs from config."""
        try:
            config = await models.Config.find_one()
            if config is None:
                return []
            return list(config.admins or [])
        except Exception as e:
            self.logger.error("get_admins failed", extra_tag="DB", exc=e)
            return []

    async def get_owner_user_id(self) -> int:
        """Get the owner Telegram user_id (protected admin)."""
        try:
            config = await models.Config.find_one()
            if config is not None and config.owner_user_id is not None:
                return config.owner_user_id
            return app_config.DEFAULT_ADMIN
        except Exception as e:
            self.logger.error("get_owner_user_id failed", extra_tag="DB", exc=e)
            return app_config.DEFAULT_ADMIN



    async def get_admin_users(self):
        """Get TelegramUser documents for all configured admins."""
        try:
            admin_ids = await self.get_admins()
            if not admin_ids:
                return []

            admin_id_set = set(admin_ids)
            telegram_users = await self.find_all_telegram_users(
                exclude_archived=False,
                exclude_disabled=False,
            ) or []
            return [user for user in telegram_users if user.user_id in admin_id_set]
        except Exception as e:
            self.logger.error("get_admin_users failed", extra_tag="DB", exc=e)
            return []

    async def get_registered_admins(self) -> list[int]:
        """Get admin user IDs that are registered Telegram users."""
        try:
            admin_users = await self.get_admin_users()
            if not admin_users:
                return []
            return [admin.user_id for admin in admin_users if admin.user_id is not None]
        except Exception as e:
            self.logger.error("get_registered_admins failed", extra_tag="DB", exc=e)
            return []

    async def add_admin(self, user_id: int) -> bool:
        """Add a user to the admin list."""
        try:
            config = await models.Config.find_one()
            if config:
                if user_id not in config.admins:
                    config.admins.append(user_id)
                    await config.save()
                    self.logger.info(f"Added user {user_id} to admin list")
                    return True
                else:
                    self.logger.debug(f"User {user_id} is already an admin")
                    return True
            return False
        except Exception as e:
            self.logger.error(f"add_admin failed (user_id={user_id})", extra_tag="DB", exc=e)
            return False

    async def remove_admin(self, user_id: int) -> bool:
        """Remove a user from the admin list."""
        try:
            config = await models.Config.find_one()
            owner_id = app_config.DEFAULT_ADMIN
            if config is not None and config.owner_user_id is not None:
                owner_id = config.owner_user_id

            if user_id == owner_id:
                self.logger.warning(
                    f"Refused to revoke admin from owner (user_id={user_id})",
                    extra_tag="AUTH",
                )
                return False

            if config and user_id in config.admins:
                config.admins.remove(user_id)
                await config.save()
                self.logger.info(f"Removed user {user_id} from admin list")
                return True
            return False
        except Exception as e:
            self.logger.error(f"remove_admin failed (user_id={user_id})", extra_tag="DB", exc=e)
            return False

    async def get_log_settings(self) -> Optional[Dict[str, Any]]:
        """Get logging settings from AppSettings."""
        try:
            app_settings = await models.AppSettings.find_one()
            if app_settings:
                return {
                    "log_level": app_settings.logging.level,
                    "log_show_time": app_settings.logging.show_time,
                    "log_show_caller": app_settings.logging.show_caller,
                    "log_show_class": app_settings.logging.show_class,
                    "log_module_overrides": dict(getattr(app_settings.logging, "module_overrides", {}) or {}),
                }
            return None
        except Exception as e:
            self.logger.error("get_log_settings failed", extra_tag="DB", exc=e)
            return None

    async def update_log_settings(self, **kwargs) -> bool:
        """
        Update logging settings in AppSettings.
        
        Args:
            log_level: Optional log level (TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL)
            log_show_time: Optional boolean for time display
            log_show_caller: Optional boolean for caller context display
            log_show_class: Optional boolean for class name display
            
        Returns:
            True if successful, False otherwise
        """
        try:
            settings = await models.AppSettings.find_one()
            if not settings:
                # Create default settings if they don't exist
                settings = models.AppSettings()
                await settings.insert()
            
            # Update only provided fields
            if "log_level" in kwargs:
                settings.logging.level = kwargs["log_level"].upper()
                log_settings.level = settings.logging.level
                # Root logger stays at TRACE; dynamic filter handles thresholds.
                
            if "log_show_time" in kwargs:
                settings.logging.show_time = kwargs["log_show_time"]
                log_settings.show_time = settings.logging.show_time
                
            if "log_show_caller" in kwargs:
                settings.logging.show_caller = kwargs["log_show_caller"]
                log_settings.show_caller = settings.logging.show_caller
            
            if "log_show_class" in kwargs:
                settings.logging.show_class = kwargs["log_show_class"]
                log_settings.show_class = settings.logging.show_class

            if "log_module_overrides" in kwargs:
                overrides = kwargs["log_module_overrides"] or {}
                settings.logging.module_overrides = dict(overrides)
                log_settings.module_overrides = dict(settings.logging.module_overrides)
            
            await settings.save()
            self.logger.info(f"Updated log settings: {kwargs}")
            return True
        except Exception as e:
            self.logger.error(f"update_log_settings failed (settings={kwargs})", extra_tag="DB", exc=e)
            return False

    async def get_notification_settings(self) -> Optional[Dict[str, Any]]:
        """Get notification settings from AppSettings."""
        try:
            app_settings = await models.AppSettings.find_one()
            if app_settings:
                return {
                    "notifications_enabled": app_settings.notifications.enabled,
                    "notifications_silent": app_settings.notifications.silent
                }
            return None
        except Exception as e:
            self.logger.error("get_notification_settings failed", extra_tag="DB", exc=e)
            return None

    async def get_effective_notification_settings(
        self,
        user_id: int,
        *,
        force_silent: bool = False,
        notification_settings: Dict[str, Any] | None = None,
        telegram_user: Any | None = None,
        user_settings: Any | None = None,
    ) -> EffectiveNotificationPolicy:
        resolved_notification_settings = notification_settings
        if resolved_notification_settings is None:
            resolved_notification_settings = await self.get_notification_settings() or {
                "notifications_enabled": True,
                "notifications_silent": False,
            }

        if not resolved_notification_settings.get("notifications_enabled", True):
            return EffectiveNotificationPolicy(can_send=False, silent=False, blocked_reason="app_notifications_disabled")

        resolved_telegram_user = telegram_user
        if resolved_telegram_user is None:
            resolved_telegram_user = await self.find_user_by_id(user_id)

        if resolved_telegram_user is None:
            return EffectiveNotificationPolicy(can_send=False, silent=False, blocked_reason="user_not_found")

        if resolved_telegram_user.is_archived:
            return EffectiveNotificationPolicy(can_send=False, silent=False, blocked_reason="user_archived")

        if resolved_telegram_user.is_disabled:
            return EffectiveNotificationPolicy(can_send=False, silent=False, blocked_reason="user_disabled")

        resolved_user_settings = user_settings
        if resolved_user_settings is None:
            resolved_user_settings = await self.get_user_settings(user_id)

        user_enabled = resolved_user_settings.notifications_enabled if resolved_user_settings else True
        if not user_enabled:
            return EffectiveNotificationPolicy(can_send=False, silent=False, blocked_reason="user_notifications_disabled")

        app_silent = bool(resolved_notification_settings.get("notifications_silent", False))
        user_silent = resolved_user_settings.notifications_silent if resolved_user_settings else False
        effective_silent = bool(force_silent or app_silent or user_silent)

        return EffectiveNotificationPolicy(can_send=True, silent=effective_silent, blocked_reason=None)

    async def update_notification_settings(self, **kwargs) -> bool:
        """
        Update notification settings in AppSettings.
        
        Args:
            notifications_enabled: Optional boolean for global notification enable/disable
            notifications_silent: Optional boolean for global silent mode
            
        Returns:
            True if successful, False otherwise
        """
        try:
            settings = await models.AppSettings.find_one()
            if not settings:
                # Create default settings if they don't exist
                settings = models.AppSettings()
                await settings.insert()
            
            # Update only provided fields
            if "notifications_enabled" in kwargs:
                settings.notifications.enabled = kwargs["notifications_enabled"]
                
            if "notifications_silent" in kwargs:
                settings.notifications.silent = kwargs["notifications_silent"]
            
            await settings.save()
            self.logger.info(f"Updated notification settings: {kwargs}")
            return True
        except Exception as e:
            self.logger.error(f"update_notification_settings failed (settings={kwargs})", extra_tag="DB", exc=e)
            return False

    async def get_debt_settings(self) -> Optional[models.DebtSettings]:
        """Get debt calculation settings from AppSettings."""
        try:
            app_settings = await models.AppSettings.find_one()
            if not app_settings:
                app_settings = models.AppSettings()
                await app_settings.insert()

            return app_settings.debt
        except Exception as e:
            self.logger.error("get_debt_settings failed", extra_tag="DB", exc=e)
            return None

    async def update_debt_settings(self, **kwargs) -> bool:
        """Update debt calculation settings in AppSettings."""
        try:
            settings = await models.AppSettings.find_one()
            if not settings:
                settings = models.AppSettings()
                await settings.insert()

            if "correction_method" in kwargs and kwargs["correction_method"] is not None:
                settings.debt.correction_method = str(kwargs["correction_method"]).strip().lower()

            if "correction_threshold" in kwargs:
                settings.debt.correction_threshold = int(kwargs["correction_threshold"])

            await settings.save()
            self.logger.info(f"Updated debt settings: {kwargs}")
            return True
        except Exception as e:
            self.logger.error(f"update_debt_settings failed (settings={kwargs})", extra_tag="DB", exc=e)
            return False

    async def get_gsheet_settings(self) -> Optional[models.GsheetSettings]:
        """Get Google Sheets synchronization settings from AppSettings."""
        try:
            app_settings = await models.AppSettings.find_one()
            if not app_settings:
                app_settings = models.AppSettings()
                await app_settings.insert()
            return app_settings.gsheet
        except Exception as e:
            self.logger.error("get_gsheet_settings failed", extra_tag="DB", exc=e)
            return None

    async def update_gsheet_settings(self, **kwargs) -> bool:
        """Update Google Sheets synchronization settings in AppSettings."""
        try:
            settings = await models.AppSettings.find_one()
            if not settings:
                settings = models.AppSettings()
                await settings.insert()

            if "periodic_sync_enabled" in kwargs and kwargs["periodic_sync_enabled"] is not None:
                settings.gsheet.periodic_sync_enabled = bool(kwargs["periodic_sync_enabled"])

            if "sync_period_minutes" in kwargs and kwargs["sync_period_minutes"] is not None:
                settings.gsheet.sync_period_minutes = int(kwargs["sync_period_minutes"])

            if "two_way_sync_enabled" in kwargs and kwargs["two_way_sync_enabled"] is not None:
                settings.gsheet.two_way_sync_enabled = bool(kwargs["two_way_sync_enabled"])

            if "sync_after_actions_enabled" in kwargs and kwargs["sync_after_actions_enabled"] is not None:
                settings.gsheet.sync_after_actions_enabled = bool(kwargs["sync_after_actions_enabled"])

            await settings.save()
            self.logger.info(f"Updated gsheet settings: {kwargs}")
            return True
        except Exception as e:
            self.logger.error(f"update_gsheet_settings failed (settings={kwargs})", extra_tag="DB", exc=e)
            return False

    async def get_snapshot_settings(self) -> Optional[models.SnapshotSettings]:
        """Get snapshot settings from AppSettings."""
        try:
            app_settings = await models.AppSettings.find_one()
            if not app_settings:
                app_settings = models.AppSettings()
                await app_settings.insert()
            return app_settings.snapshots
        except Exception as e:
            self.logger.error("get_snapshot_settings failed", extra_tag="DB", exc=e)
            return None

    async def update_snapshot_settings(self, **kwargs) -> bool:
        """Update snapshot settings in AppSettings."""
        try:
            settings = await models.AppSettings.find_one()
            if not settings:
                settings = models.AppSettings()
                await settings.insert()

            if "keep_last" in kwargs and kwargs["keep_last"] is not None:
                settings.snapshots.keep_last = int(kwargs["keep_last"])

            if "card_closed" in kwargs and kwargs["card_closed"] is not None:
                settings.snapshots.card_closed = bool(kwargs["card_closed"])

            if "session_completed" in kwargs and kwargs["session_completed"] is not None:
                settings.snapshots.session_completed = bool(kwargs["session_completed"])

            if "quick_order" in kwargs and kwargs["quick_order"] is not None:
                settings.snapshots.quick_order = bool(kwargs["quick_order"])

            if "card_created" in kwargs and kwargs["card_created"] is not None:
                settings.snapshots.card_created = bool(kwargs["card_created"])

            await settings.save()
            self.logger.info(f"Updated snapshot settings: {kwargs}")
            return True
        except Exception as e:
            self.logger.error(f"update_snapshot_settings failed (settings={kwargs})", extra_tag="DB", exc=e)
            return False

    ### User Settings ###

    async def get_user_settings(self, user_id: int) -> Optional[models.UserSettings]:
        """Get user settings by user_id, creating default settings if not found."""
        try:
            settings = await models.UserSettings.find_one(models.UserSettings.user_id == user_id)
            if not settings:
                # Create default settings for this user
                try:
                    settings = models.UserSettings(user_id=user_id)
                    await settings.insert()
                    self.logger.info(f"Created default settings for user {user_id}", extra_tag="SETTINGS")
                except Exception as create_error:
                    self.logger.error(
                        f"Error creating settings for user {user_id}: {create_error}",
                        extra_tag="SETTINGS",
                        exc=create_error,
                    )
                    # If creation failed, try to find it again (might have been created by another request)
                    settings = await models.UserSettings.find_one(models.UserSettings.user_id == user_id)
                    if not settings:
                        raise create_error
            return settings
        except Exception as e:
            self.logger.error(
                f"Exception in get_user_settings for user {user_id}: {e}",
                extra_tag="SETTINGS",
                exc=e,
            )
            self.logger.debug(f"Traceback: {traceback.format_exc()}", extra_tag="SETTINGS")
            self.logger.error(f"get_user_settings failed (user_id={user_id})", extra_tag="DB", exc=e)
            return None

    async def update_user_settings(self, user_id: int, **kwargs) -> Optional[models.UserSettings]:
        """Update user settings by user_id."""
        try:
            settings = await self.get_user_settings(user_id)
            if not settings:
                return None
            
            # Update only provided fields
            for key, value in kwargs.items():
                if hasattr(settings, key):
                    setattr(settings, key, value)
            
            await settings.save()
            self.logger.info(f"Updated settings for user {user_id}: {kwargs}", extra_tag="SETTINGS")
            return settings
        except Exception as e:
            self.logger.error(f"update_user_settings failed (user_id={user_id}, kwargs={kwargs})", extra_tag="DB", exc=e)
            return None

