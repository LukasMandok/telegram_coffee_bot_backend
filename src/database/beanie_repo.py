from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie

from .base_repo import BaseRepository
from ..models import beanie_models as models

from ..config import settings
# from ..common.helpers import hash_password



class BeanieRepository(BaseRepository):
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(BeanieRepository, cls).__new__(cls)
            cls._instance._init(*args, **kwargs)
        return cls._instance
    
    def _init(self) -> None:
        self.uri = None
        self.db = None
        self.client = None     
        
    #---------------------------
    #     Connection
    #---------------------------   
        
    async def connect(self, uri):
        self.uri = uri
        
        print("connecting to mongodb: uri", )
        # await DataBase.connect(uri = self.uri, db = "fastapi")
        self.client = AsyncIOMotorClient(self.uri)
        self.db = self.client["fastapi"]
        
        await init_beanie(self.db, document_models=[
            models.BaseUser,
            models.TelegramUser,
            models.FullUser,
            models.Password,
            models.Config])
        
        # IDEA: maybe use asyncio.create_task to run them in the background
        
        await self.ping()
        await self.getInfo()
        
        # load default values
        await self.setup_defaults()        
        
    async def close(self):
        print("closing mongodb")
        self.client.close()
        
    async def setup_defaults(self):
        password_doc = models.Password(
            hash_value = settings.DEFAULT_PASSWORD
        )
        # password_doc.set_password(settings.DEFAULT_PASSWORD)
        
        # config = await models.ConfigDocument.find_one({})
        # if config is None:
        #     config = models.ConfigDocument(password=password_doc, admin=settings.DEFAULT_ADMIN)
        #     await config.insert()
        
        # TODO: CHange the admins field to work with usernames as well? maybe.
        print("!!! Setup default config values")
        # await models.Config.find_one({}).upsert(
        #     on_insert = models.Config(
        #         password = password_doc,
        #         admins   = [settings.DEFAULT_ADMIN]
        #     )
        # )
        if not await models.Config.find_one({}):
            print("There is no entry in Config yet.")
            new_config = models.Config(
                password = password_doc,
                admins   = [settings.DEFAULT_ADMIN]
            )
            print("new config entry: ", new_config)
            await password_doc.insert()
            await new_config.insert()
    #---------------------------
    #     Helper Methods
    #---------------------------
    
    async def getInfo(self):
        print("-- database:", self.db)
        print("-- collection:", self.db.get_collection("fastapi"))
        
        
    async def ping(self):
        try: 
            self.client.admin.command('ping')
            print("Sucessfully connected to database.")
        except Exception as e:
            print(e)
            
    async def get_collection(self, collection_name):
        return self.db.get_collection(collection_name)
            
    #---------------------------
    #     Access Database
    #---------------------------
    
    ### Users ###
    
    async def find_all_users(self):
        return await models.TelegramUser.find_all()
    
    async def find_user_by_id(self, user_id):
        # return models.TelegramUser.find_one( models.TelegramUser.id == id)    
        return await models.TelegramUser.get(user_id)
    
    ### Configuration ###
    
    async def get_password(self):
        config = await models.Config.find_one(fetch_links=True)
        print("password in config ", config.get_password())
        return config.get_password()
    
    async def get_admins(self):
        return await models.Config.find_one().admins