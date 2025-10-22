import asyncio

from http import HTTPStatus
from fastapi import FastAPI, Request, Response
from contextlib import asynccontextmanager

import uvicorn

from src.config import app_config
from src.api.telethon_api import TelethonAPI
from src.routers import users, admin, coffee
from src.dependencies.dependencies import get_repo
from src.common.log import log_app_startup, log_app_shutdown, log_database_connected, log_database_connection_failed, log_database_error
from src.temp_debug_setup import run_debug_setup_if_enabled
from src.bot.settings_manager import SettingsManager
# from .middlewares.middleware import SecurityMiddleware

### connecting bot 
telethon_api = TelethonAPI(
    app_config.API_ID,
    app_config.API_HASH,
    app_config.BOT_TOKEN
)

mongodb = get_repo()
# mongodb = MongoRepository(settings.DATABASE_URL)

@asynccontextmanager
async def lifespan(app: FastAPI):
    log_app_startup()
    
    try:
        await mongodb.connect(app_config.DATABASE_URL)
        log_database_connected(app_config.DATABASE_URL)
        
        # Run debug setup (dev-only operations like defaults and passive users)
        await run_debug_setup_if_enabled()
        
        # Initialize application settings from database
        await SettingsManager.initialize_log_settings_from_db()
        
    except Exception as e:
        log_database_connection_failed(str(e))
        raise e
    
    yield 
    
    try:
        await mongodb.close()
        log_app_shutdown()
    except Exception as e:
        log_database_error("shutdown", str(e))
    
    # mongodb.connect()
    # yield 
    # mongodb.close()


# TODO: define database as global parameter dependency (probably does not work)
app = FastAPI(lifespan = lifespan)

app.include_router(users.router)
app.include_router(coffee.router)

# app.add_middleware(SecurityMiddleware)

uvicorn_server = uvicorn.Server(uvicorn.Config(app, host="localhost", port=8000))



async def run_fastapi():
    # uvicorn.run(app, host="localhost", port=8000)
    await uvicorn_server.serve()
    
async def run_telethon():
    await telethon_api.run()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    tasks = [run_fastapi(), run_telethon()]
    loop.run_until_complete(asyncio.gather(*tasks))
    loop.close()