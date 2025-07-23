import logging
import asyncio

from http import HTTPStatus
from fastapi import FastAPI, Request, Response
from contextlib import asynccontextmanager

import uvicorn

from src.config import settings
from src.api.telethon_api import TelethonAPI
from src.routers import users, admin, coffee
from src.dependencies.dependencies import get_repo
# from .middlewares.middleware import SecurityMiddleware

### logging configuration
logging.basicConfig(format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s', level=logging.WARNING)

### connecting bot 
telethon_api = TelethonAPI(
    settings.API_ID,
    settings.API_HASH,
    settings.BOT_TOKEN
)

mongodb = get_repo()
# mongodb = MongoRepository(settings.DATABASE_URL)

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await mongodb.connect(settings.DATABASE_URL)
        print("Database connected successfully")
    except Exception as e:
        print(f"Database connection failed: {e}")
        print("Cannot start application without database connection")
        raise e
    
    yield 
    
    try:
        await mongodb.close()
        print("Database connection closed")
    except Exception as e:
        print(f"Warning: Error closing database: {e}")
    
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