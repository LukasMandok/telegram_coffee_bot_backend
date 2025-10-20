# from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import AsyncMongoClient
from beanie import init_beanie
from typing import Optional
from datetime import datetime

from .base_repo import BaseRepository
from ..models import beanie_models as models
from ..models.coffee_models import CoffeeCard, CoffeeOrder, Payment, UserDebt, CoffeeSession
from ..common.log import (
    log_database_connected, log_database_connection_failed, log_database_error, log_database_disconnected,
    log_user_registration, log_performance_metric, log_app_shutdown, log_setup_database_defaults
)

from ..config import settings
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
            self.db = self.client["fastapi"]

            await init_beanie(self.db, document_models=[
                models.BaseUser,
                models.TelegramUser,
                models.PassiveUser,
                models.Password,
                models.Config,
                models.UserSettings,
                # Coffee models
                CoffeeCard,
                CoffeeOrder,
                Payment,
                UserDebt,
                CoffeeSession])

            # IDEA: maybe use asyncio.create_task to run them in the background

            await self.ping()
            await self.getInfo()

            # load default values
            await self.setup_defaults()
            
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
        print("Clearing existing configuration data...")
        await models.Config.delete_all()
        await models.Password.delete_all()
        
        print(f"Creating fresh default password for: {settings.DEFAULT_PASSWORD}")
        password_doc = models.Password(
            hash_value = settings.DEFAULT_PASSWORD
        )
        
        # Create Config with Link to password
        new_config = models.Config(
            password = password_doc,  # Beanie should auto-create the Link
            admins   = [int(settings.DEFAULT_ADMIN)]  # Convert to int
        )
        
        await password_doc.insert()
        await new_config.insert()
        
        print("Verify just safed password: ", password_doc.verify_password(settings.DEFAULT_PASSWORD))
        
        log_setup_database_defaults([int(settings.DEFAULT_ADMIN)])
        print("Fresh default configuration created successfully")

    # ---------------------------
    #     Helper Methods
    # ---------------------------

    async def getInfo(self):
        print("-- database:", self.db)
        print("-- collection:", self.db.get_collection("fastapi"))


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

    async def find_all_users(self):
        try:
            # Get both telegram users and passive users
            telegram_users = await models.TelegramUser.find_all().to_list()
            passive_users = await models.PassiveUser.find_all().to_list()
            
            all_users = telegram_users + passive_users
            log_performance_metric("find_all_users", len(all_users), "records")
            return all_users

        except Exception as e:
            log_database_error("find_all_users", str(e))
            return []

    async def find_user_by_id(self, id: int):
        print(f"DEBUG: find_user_by_id called with id={id}")
        # Telegram users are the only ones with a user_id
        telegram_user = await models.TelegramUser.find_one(models.TelegramUser.user_id == id)
        if telegram_user:
            print(f"DEBUG: Found TelegramUser: {telegram_user}")
            print(f"DEBUG: TelegramUser type: {type(telegram_user)}")
            print(f"DEBUG: TelegramUser has display_name: {hasattr(telegram_user, 'display_name')}")
        else:
            print(f"DEBUG: No TelegramUser found either")
        
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
            
            print(f"DEBUG: Creating user with phone: {phone} (type: {type(phone)})")

            display_name = await self._generate_unique_display_name(first_name, last_name)
            print(f"DEBUG: Generated display name: '{display_name}'")
            
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
        
        print(f"Updated existing user's display name from '{first_name}' to '{new_existing_display_name}'")
        
        # Generate new user's display name
        new_user_display_name = f"{first_name} {last_name[:letters_needed]}."
        return new_user_display_name

    async def create_passive_user(self, first_name: str, last_name: Optional[str] = None) -> models.PassiveUser:
        """Create a new PassiveUser with smart display name generation."""
        try:
            print(f"DEBUG: Creating passive user: {first_name} {last_name or ''}")
            
            # First check if a TelegramUser with the same name already exists
            print(f"DEBUG: Checking for existing TelegramUser with same name...")
            existing_telegram_user = await models.TelegramUser.find_one(
                models.TelegramUser.first_name == first_name,
                models.TelegramUser.last_name == last_name
            )
            
            if existing_telegram_user:
                print(f"DEBUG: Found existing TelegramUser with same name: {existing_telegram_user.display_name}")
                raise ValueError(
                    f"Cannot create passive user: A Telegram user with the name '{first_name} {last_name or ''}' already exists. "
                    f"Display name: {existing_telegram_user.display_name}"
                )
            
            # Generate unique display name
            display_name = await self._generate_unique_display_name(first_name, last_name)
            print(f"DEBUG: Generated display name: '{display_name}'")
            
            new_user = models.PassiveUser(
                first_name=first_name,
                last_name=last_name,
                display_name=display_name,
                created_at=datetime.now(),
                updated_at=datetime.now()
            )
            
            await new_user.insert()
            print(f"Created passive user: {display_name}")
            return new_user
            
        except Exception as e:
            log_database_error("create_passive_user", str(e), {"first_name": first_name, "last_name": last_name})
            raise

    async def find_passive_user_by_name(self, first_name: str, last_name: Optional[str] = None) -> models.PassiveUser | None:
        """Find a passive user by first name and last name."""
        try:
            print(f"DEBUG: Searching for passive user: first_name='{first_name}', last_name='{last_name}'")
            
            # Debug: List all passive users
            # all_passive_users = await models.PassiveUser.find_all().to_list()
            # print(f"DEBUG: All passive users in database:")
            # for user in all_passive_users:
            #     print(f"  - first_name='{user.first_name}', last_name='{user.last_name}', display_name='{user.display_name}'")
            
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
            print(f"DEBUG: Converting passive user '{passive_user.display_name}' to telegram user")
            
            # Use provided names or fall back to passive user's names
            final_first_name = first_name if first_name is not None else passive_user.first_name
            final_last_name = last_name if last_name is not None else passive_user.last_name
            
            print(f"DEBUG: Using names - first: '{final_first_name}', last: '{final_last_name}'")
            
            # Use the existing display name from the passive user (it's already unique and correct)
            display_name = passive_user.display_name
            print(f"DEBUG: Using existing display name: '{display_name}'")
            
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
        print("beanie_repo: get_password - fetching config")
        config = await models.Config.find_one()
        if config:
            print("beanie_repo: get_password - retrieving password")
            password = await config.get_password()
            print("password in config ", password)
            return password
        else:
            print("beanie_repo: get_password - no config found")
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
                    print(f"Added user {user_id} to admin list")
                    return True
                else:
                    print(f"User {user_id} is already an admin")
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
                print(f"Removed user {user_id} from admin list")
                return True
            return False
        except Exception as e:
            log_database_error("remove_admin", str(e), {"user_id": user_id})
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
                    print(f"[SETTINGS] Created default settings for user {user_id}")
                except Exception as create_error:
                    print(f"[SETTINGS] Error creating settings for user {user_id}: {create_error}")
                    # If creation failed, try to find it again (might have been created by another request)
                    settings = await models.UserSettings.find_one(models.UserSettings.user_id == user_id)
                    if not settings:
                        raise create_error
            return settings
        except Exception as e:
            import traceback
            print(f"[SETTINGS] Exception in get_user_settings for user {user_id}: {e}")
            print(f"[SETTINGS] Traceback: {traceback.format_exc()}")
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
            print(f"Updated settings for user {user_id}: {kwargs}")
            return settings
        except Exception as e:
            log_database_error("update_user_settings", str(e), {"user_id": user_id, "kwargs": kwargs})
            return None

