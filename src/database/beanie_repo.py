from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie

from .base_repo import BaseRepository
from ..models import beanie_models as models
from ..models.coffee_models import CoffeeCard, CoffeeOrder, Payment, UserDebt, CoffeeSession
from ..common.log import (
    log_database_connected, log_database_connection_failed, log_database_error, log_database_disconnected,
    log_user_registration, log_performance_metric, log_app_shutdown, log_setup_database_defaults
)

from ..config import settings
# from ..common.helpers import hash_password


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
            
            log_database_connected(self.uri)
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
        print("setup default password to: ", settings.DEFAULT_PASSWORD)
        password_doc = models.Password(
            hash_value = settings.DEFAULT_PASSWORD
        )
        # password_doc.set_password(settings.DEFAULT_PASSWORD)

        # config = await models.ConfigDocument.find_one({})
        # if config is None:
        #     config = models.ConfigDocument(password=password_doc, admin=settings.DEFAULT_ADMIN)
        #     await config.insert()

        # TODO: Change the admins field to work with usernames as well? maybe.
        # await models.Config.find_one({}).upsert(
        #     on_insert = models.Config(
        #         password = password_doc,
        #         admins   = [settings.DEFAULT_ADMIN]
        #     )
        # )
        if not await models.Config.find_one({}):
            new_config = models.Config(
                password = password_doc,
                admins   = [settings.DEFAULT_ADMIN]
            )
            await password_doc.insert()
            await new_config.insert()
            
            log_setup_database_defaults([settings.DEFAULT_ADMIN])
        else:
            pass

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

    async def find_user_by_id(self, user_id: int):
        return models.TelegramUser.find_one( models.TelegramUser.user_id == user_id )  
        # return await models.TelegramUser.get(id)

    ### Configuration ###

    async def get_password(self):
        print("beanie_repo: get_password - fetching config")
        config = await models.Config.find_one(fetch_links=True) 
        print("beanie_repo: get_password - retrieving password")
        print("password in config ", config.get_password())
        return config.get_password()

    async def get_admins(self):
        config = await models.Config.find_one()
        print("beanie_repo - config:", config)
        admins = config.admins
        print("beanie_repo - admins:", admins)
        return admins
