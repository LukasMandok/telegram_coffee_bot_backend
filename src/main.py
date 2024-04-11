from http import HTTPStatus
from fastapi import FastAPI, Request, Response

import uvicorn

from contextlib import asynccontextmanager
from telegram import Update
from telegram.ext import Application, CommandHandler
from telegram.ext._contexttypes import ContextTypes

from api import app

from bot.ptb import ptb
from bot import handlers, commands

from config import TELEGRAM_TOKEN, WEBHOOK_URL

from common.log import logger
# Initialize telegram bot -> move to another file
ptb = (
    Application.builder()
    .updater(None)
    .token(TELEGRAM_TOKEN) # TODO: get token from environmental variables
    .read_timeout(7)
    .get_updates_read_timeout(42)
    .build()
)

@asynccontextmanager
async def lifespan(_: FastAPI):
    await ptb.bot.setWebhook(WEBHOOK_URL) # TODO: get webook url from env vars
    async with ptb:
            await  ptb.start()
            yield
            await ptb.stop()
            
            
# Initialize FastAPI app
app = FastAPI(lifespan = lifespan)

@app.post("/")
async def process_update(request: Request):
    req = await  request.json()
    update = Update.de_json(req, ptb.bot)
    await ptb.process_update(update)
    return Response(status_code=HTTPStatus.OK)

# Start Handler:
async def start(update, _: ContextTypes.DEFAULT_TYPE):
    # send a message when command /start is issued
    await update.message.reply_text("starting ...")

ptb.add_handler(CommandHandler("start", start))