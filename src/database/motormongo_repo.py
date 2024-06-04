from motormongo import DataBase, get_db

import src.models.motormodels as models

from ..config import settings
from ..common.helpers import hash_password

from .base_repo import BaseRepository

class MotorMongoRepository(BaseRepository):
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
        await DataBase.connect("mongodb://admin:password123@localhost:27017/fastapi", db = "fastapi")

        self.db = await get_db()
        self.client = self.db.client
        
        self.ping()
        
        # load default values
        # self.setup_defaults()        
        
    async def close(self):
        print("closing mongodb")
        await DataBase.close()
        
    async def setup_defaults(self):
        config, created = await models.ConfigDocument.find_one_or_create(
            query = {}, 
            defaults = {
                'password': {'hash_value' : hash_password(settings.DEFAULT_PASSWORD)},
                'admin': settings.DEFAULT_ADMIN
                }
            )
        
    #---------------------------
    #     Helper Methods
    #---------------------------
        
    def ping(self):
        try: 
            self.client.admin.command('ping')
            print("Sucessfully connected to database.")
        except Exception as e:
            print(e)
            
    #---------------------------
    #     Access Database
    #---------------------------
    
    def find_all_users(self):
        return models.TelegramUserDocument.find_all()
    
    def find_user_by_id(self, id):
        return models.TelegramUserDocument.find_one(id)
    
    def get_password(self):
        return models.ConfigDocument.find_one()