import logging
import asyncio

from http import HTTPStatus
from fastapi import FastAPI, Request, Response
from contextlib import asynccontextmanager

import uvicorn

# from api import app

# from bot.ptb import ptb
# from bot import handlers, commands

from .config import settings

# from database.mongo import MongoDB
from .database.motormongo_repo import MotorMongoRepository
from .api.telethon_api import TelethonAPI

from .api.routes import router


### logging configuration
logging.basicConfig(format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s', level=logging.WARNING)

### connecting bot 
telethon_api = TelethonAPI(
    settings.API_ID,
    settings.API_HASH,
    settings.BOT_TOKEN
)
mongodb = MotorMongoRepository(settings.DATABASE_URL)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await mongodb.connect()
    yield 
    await mongodb.close()

app = FastAPI(lifespan = lifespan)
app.include_router(router)

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