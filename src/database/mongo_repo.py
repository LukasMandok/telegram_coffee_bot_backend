from pymongo.mongo_client import MongoClient

class MongoDBRepository():
    def __init__(self, url):
        self.client = MongoClient(url)
        self.db = self.client.get_database()
        self.ping()
        self.getInfo()

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