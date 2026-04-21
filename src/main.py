import asyncio
from typing import Any, cast

from fastapi import FastAPI
from contextlib import asynccontextmanager

import uvicorn

from src.config import app_config
from src.api.telethon_api import TelethonAPI
from src.routers import users, admin, coffee
from src.dependencies.dependencies import get_repo
from src.common.log import Logger
from src.temp_debug_setup import run_debug_setup_if_enabled
from src.bot.settings_manager import SettingsManager
from src.services.gsheet_sync import run_periodic_gsheet_sync, warmup_gsheet_api
from src.services.weekly_snapshots import run_periodic_weekly_full_snapshots
# from .middlewares.middleware import SecurityMiddleware

logger = Logger("Main")

mongodb = get_repo()
# mongodb = MongoRepository(settings.DATABASE_URL)

### connecting bot 
telethon_api = TelethonAPI(
    app_config.API_ID,
    app_config.API_HASH,
    app_config.BOT_TOKEN,
    repo=mongodb,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Coffee Bot application starting up...", extra_tag="APP")

    telethon_task: asyncio.Task[None] | None = None

    gsheet_stop_event = asyncio.Event()
    gsheet_task: asyncio.Task[None] | None = None

    weekly_snapshot_stop_event = asyncio.Event()
    weekly_snapshot_task: asyncio.Task[None] | None = None
    
    try:
        database_url = app_config.DATABASE_URL
        if not database_url:
            raise RuntimeError("DATABASE_URL is not set")

        await mongodb.connect(database_url)
        logger.info(f"Connected to MongoDB (uri={database_url})", extra_tag="DB")

        # Run debug setup (dev-only operations like defaults and passive users)
        await run_debug_setup_if_enabled()
        
        # Initialize application settings from database
        await SettingsManager.initialize_log_settings_from_db()

        # Start Telethon bot in the background (do not block lifespan startup)
        telethon_task = asyncio.create_task(telethon_api.run())

        # Periodic one-way export to Google Sheets (optional)
        if app_config.GSHEET_SYNC_ENABLED:
            try:
                await warmup_gsheet_api()
            except Exception as e:
                logger.error(
                    "Google Sheets warmup failed; periodic sync disabled",
                    extra_tag="GSHEET",
                    exc=e,
                )
            else:
                gsheet_task = asyncio.create_task(
                    run_periodic_gsheet_sync(stop_event=gsheet_stop_event)
                )

        if mongodb.snapshot_manager is not None:
            weekly_snapshot_task = asyncio.create_task(
                run_periodic_weekly_full_snapshots(
                    stop_event=weekly_snapshot_stop_event,
                    snapshot_manager=mongodb.snapshot_manager,
                )
            )
        
    except Exception as e:
        logger.error("Startup failed", extra_tag="APP", exc=e)
        raise
    
    yield 
    
    try:
        gsheet_stop_event.set()
        if gsheet_task:
            await gsheet_task

        weekly_snapshot_stop_event.set()
        if weekly_snapshot_task:
            await weekly_snapshot_task

        if telethon_task is not None:
            try:
                await asyncio.wait_for(cast(Any, telethon_api.bot).disconnect(), timeout=10)
            except Exception:
                pass

            try:
                await asyncio.wait_for(telethon_task, timeout=10)
            except Exception:
                telethon_task.cancel()

        await mongodb.close()
        logger.info("Coffee Bot application shutting down...", extra_tag="APP")
    except Exception as e:
        logger.error("Shutdown failed", extra_tag="APP", exc=e)
    
    # mongodb.connect()
    # yield 
    # mongodb.close()


# TODO: define database as global parameter dependency (probably does not work)
app = FastAPI(lifespan = lifespan)

app.include_router(users.router)
app.include_router(coffee.router)

# app.add_middleware(SecurityMiddleware)

uvicorn_server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8000))



async def run_fastapi():
    # uvicorn.run(app, host="localhost", port=8000)
    await uvicorn_server.serve()

async def main() -> None:
    await run_fastapi()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # asyncio.run() raises KeyboardInterrupt after cancelling the main task.
        # main() already performs shutdown; keep terminal exit clean.
        pass