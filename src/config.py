import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# TELEGRAM BOT SPECIFIC CONFIG
API_ID      = os.environ.get("API_ID")
API_HASH    = os.environ.get("API_HASH")
BOT_TOKEN   = os.environ.get("BOT_TOKEN")
BOT_HOST    = os.environ.get("BOT_HOST")
GSHEET_SSID = os.environ.get("GSHEET_SSID")

# Ensure all required variables are present
required_vars = ["API_ID", "API_HASH", "BOT_TOKEN", "BOT_HOST", "GSHEET_SSID"]
missing_vars = [var for var in required_vars if not os.environ.get(var)]
if missing_vars:
    raise EnvironmentError(f"Missing required environment variables: {', '.join(missing_vars)}")