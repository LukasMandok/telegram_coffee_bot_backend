from pydantic import PrivateAttr
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    API_ID: str
    API_HASH: str
    BOT_TOKEN: str
    BOT_HOST: str

    # MONGODB

    DATABASE_URL: str
    MONGO_INITDB_DATABASE: str
    MONGO_INITDB_ROOT_USERNAME: str
    MONGO_INITDB_ROOT_PASSWORD: str
    
    # CONFIGURATION
    
    DEFAULT_PASSWORD: str
    DEFAULT_ADMIN: str
    
    # Google Sheets
    GSHEET_SSID: str
    
    # Google API
    SERVICE_ACCOUNT_EMAIL: str
    SERVICE_ACCOUNT_PRIVATE_KEY: str
    _SERVICE_ACCOUNT_PRIVATE_KEY: str = PrivateAttr()
    PROJECT_ID: str
    
    @property
    def SERVICE_ACCOUNT_PRIVATE_KEY(self):
        return self._SERVICE_ACCOUNT_PRIVATE_KEY.replace('\\n', '\n')

    @SERVICE_ACCOUNT_PRIVATE_KEY.setter
    def SERVICE_ACCOUNT_PRIVATE_KEY(self, value):
        self._SERVICE_ACCOUNT_PRIVATE_KEY = value
        
        
    class Config:
        env_file = './.env'


settings = Settings()

# import os
# from dotenv import load_dotenv

# # Load environment variables from .env file
# load_dotenv()

# # TELEGRAM BOT SPECIFIC CONFIG
# API_ID      = os.environ.get("API_ID")
# API_HASH    = os.environ.get("API_HASH")
# BOT_TOKEN   = os.environ.get("BOT_TOKEN")
# BOT_HOST    = os.environ.get("BOT_HOST")
# GSHEET_SSID = os.environ.get("GSHEET_SSID")

# # Ensure all required variables are present
# required_vars = ["API_ID", "API_HASH", "BOT_TOKEN", "BOT_HOST", "GSHEET_SSID"]
# missing_vars = [var for var in required_vars if not os.environ.get(var)]
# if missing_vars:
#     raise EnvironmentError(f"Missing required environment variables: {', '.join(missing_vars)}")
