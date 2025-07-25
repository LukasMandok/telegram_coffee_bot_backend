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
        return await models.TelegramUser.find_one(models.TelegramUser.user_id == id)
        # return await models.TelegramUser.get(id)
    
    async def create_telegram_user(self, user_id: int, username: str, first_name: str, last_name: Optional[str] = None):
        """Create a new TelegramUser in the database."""
        try:
            # Check if user already exists
            existing_user = await self.find_user_by_id(user_id)
            if existing_user:
                log_database_error("create_user", f"User with ID {user_id} already exists")
                return existing_user
            
            new_user = models.TelegramUser(
                user_id=user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                last_login=datetime.now(),
                created_at=datetime.now(),
                updated_at=datetime.now()
            )
            
            await new_user.insert()
            log_user_registration(user_id, username)
            return new_user
            
        except Exception as e:
            log_database_error("create_telegram_user", str(e), {"user_id": user_id, "username": username})
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
        config = await models.Config.find_one()
        if config:
            print("beanie_repo - config:", config)
            admins = config.admins
            print("beanie_repo - admins:", admins)
            return admins
        else:
            print("beanie_repo - no config found")
            return []
