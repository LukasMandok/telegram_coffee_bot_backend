# from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import AsyncMongoClient
import logging
from beanie import init_beanie
from typing import Optional, Dict, Any
from datetime import datetime

from .base_repo import BaseRepository
from ..models import beanie_models as models
from ..models.coffee_models import CoffeeCard, CoffeeOrder, Payment, UserDebt, CoffeeSession
from ..common.log import (
    log_database_connected, log_database_connection_failed, log_database_error, log_database_disconnected,
    log_user_registration, log_performance_metric, log_app_shutdown, log_setup_database_defaults, Logger
)

from ..config import app_config
from ..common.helpers import hash_password


class BeanieRepository(BaseRepository):
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(BeanieRepository, cls).__new__(cls)
            cls._instance.__init__(*args, **kwargs)
        return cls._instance

    def __init__(self) -> None:
        self.uri = None
        self.db = None
        self.logger = Logger("BeanieRepository")
        self.client = None

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
                # Coffee models
                CoffeeCard,
                CoffeeOrder,
                Payment,
                UserDebt,
                CoffeeSession])

            # IDEA: maybe use asyncio.create_task to run them in the background

            await self.ping()
            
        except Exception as e:
            log_database_connection_failed(str(e))
            raise

    async def close(self):
        try:
            if self.client:
                self.client.close()
            log_database_disconnected()
        except Exception as e:
            log_database_error("close_connection", str(e))

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
            password = password_doc,  # Beanie should auto-create the Link
            admins   = [int(app_config.DEFAULT_ADMIN)]  # Convert to int
        )
        
        # Create default Settings
        app_settings = models.AppSettings()
        
        await password_doc.insert()
        await new_config.insert()
        await app_settings.insert()
        
        log_setup_database_defaults([int(app_config.DEFAULT_ADMIN)])
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
            log_database_error("ping", str(e))


    async def get_collection(self, collection_name):
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
            log_performance_metric("find_all_users", len(all_users), "records")
            return all_users

        except Exception as e:
            log_database_error("find_all_users", str(e))
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
            
            log_performance_metric("find_all_telegram_users", len(telegram_users), "records")
            return telegram_users

        except Exception as e:
            log_database_error("find_all_telegram_users", str(e))
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
            log_database_error("is_user_admin", str(e), {"user_id": user_id})
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
    ):
        """Create a new TelegramUser in the database."""
        try:
            # Check if user already exists
            existing_user = await self.find_user_by_id(user_id)
            if existing_user:
                log_database_error("create_user", f"User with ID {user_id} already exists")
                return existing_user

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
            log_user_registration(user_id, username)
            return new_user
            
        except Exception as e:
            log_database_error("create_telegram_user", str(e), {"user_id": user_id, "username": username})
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
            log_database_error("create_passive_user", str(e), {"first_name": first_name, "last_name": last_name})
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
            log_database_error("find_passive_user_by_name", str(e), {"first_name": first_name, "last_name": last_name})
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
    ) -> models.TelegramUser:
        """Convert a PassiveUser to a TelegramUser by transferring data and deleting the passive user."""
        try:
            # Use provided names or fall back to passive user's names
            final_first_name = first_name if first_name is not None else passive_user.first_name
            final_last_name = last_name if last_name is not None else passive_user.last_name
            # Use the existing display name from the passive user (it's already unique and correct)
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

            log_user_registration(user_id, username)
            return new_telegram_user
            
        except Exception as e:
            log_database_error("convert_passive_to_telegram_user", str(e), {"user_id": user_id, "passive_user_id": str(passive_user.id)})
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

    async def get_admins(self):
        """Get the list of admin user IDs from config."""
        try:
            config = await models.Config.find_one()
            if config and config.admins:
                return config.admins
            return []
        except Exception as e:
            log_database_error("get_admins", str(e))
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
            log_database_error("add_admin", str(e), {"user_id": user_id})
            return False

    async def remove_admin(self, user_id: int) -> bool:
        """Remove a user from the admin list."""
        try:
            config = await models.Config.find_one()
            if config and user_id in config.admins:
                config.admins.remove(user_id)
                await config.save()
                self.logger.info(f"Removed user {user_id} from admin list")
                return True
            return False
        except Exception as e:
            log_database_error("remove_admin", str(e), {"user_id": user_id})
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
                    "log_show_class": app_settings.logging.show_class
                }
            return None
        except Exception as e:
            log_database_error("get_log_settings", str(e))
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
            from ..common.log import log_settings
            
            settings = await models.AppSettings.find_one()
            if not settings:
                # Create default settings if they don't exist
                settings = models.AppSettings()
                await settings.insert()
            
            # Update only provided fields
            if "log_level" in kwargs:
                settings.logging.level = kwargs["log_level"].upper()
                log_settings.level = settings.logging.level
                # Update the root logger level
                level_map = {
                    'TRACE': 5,
                    'DEBUG': logging.DEBUG,
                    'INFO': logging.INFO,
                    'WARNING': logging.WARNING,
                    'ERROR': logging.ERROR,
                    'CRITICAL': logging.CRITICAL
                }
                logging.root.setLevel(level_map.get(settings.logging.level, logging.INFO))
                
            if "log_show_time" in kwargs:
                settings.logging.show_time = kwargs["log_show_time"]
                log_settings.show_time = settings.logging.show_time
                
            if "log_show_caller" in kwargs:
                settings.logging.show_caller = kwargs["log_show_caller"]
                log_settings.show_caller = settings.logging.show_caller
            
            if "log_show_class" in kwargs:
                settings.logging.show_class = kwargs["log_show_class"]
                log_settings.show_class = settings.logging.show_class
            
            await settings.save()
            self.logger.info(f"Updated log settings: {kwargs}")
            return True
        except Exception as e:
            log_database_error("update_log_settings", str(e), {"settings": kwargs})
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
            log_database_error("get_notification_settings", str(e))
            return None

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
            log_database_error("update_notification_settings", str(e), {"settings": kwargs})
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
                    self.logger.error(f"Error creating settings for user {user_id}: {create_error}", extra_tag="SETTINGS", exc_info=create_error)
                    # If creation failed, try to find it again (might have been created by another request)
                    settings = await models.UserSettings.find_one(models.UserSettings.user_id == user_id)
                    if not settings:
                        raise create_error
            return settings
        except Exception as e:
            import traceback
            self.logger.error(f"Exception in get_user_settings for user {user_id}: {e}", extra_tag="SETTINGS", exc_info=e)
            self.logger.debug(f"Traceback: {traceback.format_exc()}", extra_tag="SETTINGS")
            log_database_error("get_user_settings", str(e), {"user_id": user_id})
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
            log_database_error("update_user_settings", str(e), {"user_id": user_id, "kwargs": kwargs})
            return None

