from pymongo.mongo_client import MongoClient

from .base_repo import BaseRepository


class MongoRepository(BaseRepository):
    def __init__(self, uri):
        self.uri = uri
        
        self.client = None
        self.db = None
        
    #---------------------------
    #     Connection
    #---------------------------   
        
    def connect(self):
        self.client = MongoClient(self.uri)
        self.db = self.client.get_database()
        
        self.ping()
        self.getInfo()
        
    def close(self):
        self.client.close()
    
    #---------------------------
    #     Helper Methods
    #---------------------------

    def getInfo(self):
        print(self.db)
        print(self.db.get_collection("fastapi"))

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
    
    def find_user_by_id(self, id):
        pass
    
    def get_password(self):
        pass
    