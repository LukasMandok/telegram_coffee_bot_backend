import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# TELEGRAM BOT SPECIFIC CONFIG
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
BOT_HOST        = os.environ.get("BOT_HOST")

# GSHEET_SSID    = os.environ.get("GSHEET_SSID")