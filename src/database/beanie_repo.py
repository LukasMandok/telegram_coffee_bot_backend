import motor.motor_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie

import src.models.beanie_models as models

from ..config import settings
# from ..common.helpers import hash_password

from .base_repo import BaseRepository

class BeanieRepository(BaseRepository):
    def __init__(self, uri) -> None:
        self.uri = uri
        
        self.db = None
        self.client = None     
        
    #---------------------------
    #     Connection
    #---------------------------   
        
    async def connect(self):
        print("connecting to mongodb: uri", self.uri)
        # await DataBase.connect(uri = self.uri, db = "fastapi")
        self.client = AsyncIOMotorClient(self.uri)
        self.db = self.client["fastapi"]
        
        await init_beanie(self.db, document_models=[
            models.BaseUserDocument,
            models.TelegramUserDocument,
            models.FullUserDocument,
            models.ConfigDocument])
        
        self.ping()
        self.getInfo()
        
        # load default values
        # self.setup_defaults()        
        
    async def close(self):
        print("closing mongodb")
        self.client.close()
        
    async def setup_defaults(self):
        password_doc = models.PasswordDocument(
            password = settings.DEFAULT_PASSWORD
        )
        # password_doc.set_password(settings.DEFAULT_PASSWORD)
        
        # config = await models.ConfigDocument.find_one({})
        # if config is None:
        #     config = models.ConfigDocument(password=password_doc, admin=settings.DEFAULT_ADMIN)
        #     await config.insert()
        
        await models.ConfigDocument.find_one({}).upsert(
            on_insert = models.ConfigDocument(
                password = password_doc,
                admin    = settings.DEFAULT_ADMIN
            )
        )
    
    #---------------------------
    #     Helper Methods
    #---------------------------
    
    def getInfo(self):
        print("-- database:", self.db)
        print("-- collection:", self.db.get_collection("fastapi"))
        
        
    def ping(self):
        try: 
            self.client.admin.command('ping')
            print("Sucessfully connected to database.")
        except Exception as e:
            print(e)
            
    def get_collection(self, collection_name):
        return self.db.get_collection(collection_name)
            
    #---------------------------
    #     Access Database
    #---------------------------
    
    def find_all_users(self):
        pass
        # return models.TelegramUserDocument.find_all()
    
    def find_user_by_id(self, id):
        pass
        # return models.TelegramUserDocument.find_one(id)
    
    def get_password(self):
        pass
        # return models.ConfigDocument.find_one()