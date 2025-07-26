from motor.motor_asyncio import AsyncIOMotorClient
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
            self.client = AsyncIOMotorClient(self.uri)
            self.db = self.client["fastapi"]

            await init_beanie(self.db, document_models=[
                models.BaseUser,
                models.TelegramUser,
                models.FullUser,
                models.Password,
                models.Config,
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
            users = await models.TelegramUser.find_all().to_list()
            log_performance_metric("find_all_users", len(users), "records")
            return users
        except Exception as e:
            log_database_error("find_all_users", str(e))
            return []

    async def find_user_by_id(self, id: int):
    async def is_user_admin(self, user_id: int) -> bool:
        """Check if a user is an admin by looking up their user_id in config."""
        try:
            admins = await self.get_admins()
            return user_id in admins if admins else False
        except Exception as e:
            log_database_error("is_user_admin", str(e), {"user_id": user_id})
            return False
    
    async def create_telegram_user(self, user_id: int, username: str, first_name: str, last_name: Optional[str] = None, phone: Optional[str] = None, photo_id: Optional[int] = None, lang_code: str = "en"):
        """Create a new TelegramUser in the database."""
        try:
            # Check if user already exists
            existing_user = await self.find_user_by_id(user_id)
            if existing_user:
                log_database_error("create_user", f"User with ID {user_id} already exists")
                return existing_user
            
            print(f"DEBUG: Creating user with phone: {phone} (type: {type(phone)})")
            
            new_user = models.TelegramUser(
                user_id=user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                phone=phone,  # Now it's safe to include None values
                photo_id=photo_id,
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

    async def _generate_unique_display_name(self, first_name: str, last_name: Optional[str] = None) -> str:
        """
        Generate a unique display name by progressively adding characters from last name.
        
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
        
        existing_user = await models.FullUser.find_one(models.FullUser.display_name == candidate_name)
        
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

    async def create_full_user(self, user_id: int, username: str, first_name: str, last_name: Optional[str] = None, phone: Optional[str] = None, photo_id: Optional[int] = None, lang_code: str = "en"):
        """Create a new FullUser with smart display name generation."""
        try:
            print(f"DEBUG: Creating full user with phone: {phone} (type: {type(phone)})")
            
            # Generate unique display name
            display_name = await self._generate_unique_display_name(first_name, last_name)
            print(f"DEBUG: Generated display name: '{display_name}'")
            
            new_user = models.FullUser(
                user_id=user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                phone=phone,
                photo_id=photo_id,
                display_name=display_name,
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
            log_database_error("create_full_user", str(e), {"user_id": user_id, "username": username})
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
