# Telegram Coffee Bot Backend - Development Guide

## Project Structure
This is a FastAPI-based backend for a Telegram coffee ordering bot using:
- **FastAPI** for REST API
- **Telethon** for Telegram bot integration
- **Beanie ODM** with MongoDB for data persistence
- **Google Sheets API** for data backup/visualization

## Environment Setup

### 1. Virtual Environment
```bash
# Create virtual environment
python -m venv venv

# Activate on Windows
venv\Scripts\activate

# Activate on macOS/Linux
source venv/bin/activate
```

### 2. Install Dependencies
```bash
pip install -r src/requirements.txt
```

### 3. Environment Variables
Create `.env` file with:
```
# Telegram
API_ID=your_api_id
API_HASH=your_api_hash
BOT_TOKEN=your_bot_token
BOT_HOST=http://localhost:8000

# MongoDB
MONGO_HOST=localhost
MONGO_PORT=27017
MONGO_INITDB_ROOT_USERNAME=admin
MONGO_INITDB_ROOT_PASSWORD=change_me
MONGO_INITDB_DATABASE=telegram_bot

# App
DEFAULT_ADMIN=your_telegram_user_id
DEFAULT_PASSWORD=change_me
DEBUG_MODE=False
LOG_LEVEL=INFO

# Google Sheets (optional)
GSHEET_SSID=your_spreadsheet_id
SERVICE_ACCOUNT_EMAIL=your_service_account_email
SERVICE_ACCOUNT_PRIVATE_KEY=-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n
PROJECT_ID=your_gcp_project_id
```

Tip: Use `.env.example` as the source of truth for variables.

## Running the Application

### Development Server
```bash
# Start the FastAPI server
python -m src.main

# Alternative with uvicorn directly
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

### Database Setup
MongoDB should be running locally on port 27017, or update `MONGO_HOST` / `MONGO_PORT` in `.env`.

## Key Components

### Models (src/models/)
- `beanie_models.py` - User models (TelegramUser, FullUser)
- `coffee_models.py` - Coffee system models (CoffeeCard, CoffeeOrder, UserDebt, Payment)

### Handlers (src/handlers/)
- `coffee_handlers.py` - Business logic for coffee operations

### API Routes (src/routers/)
- `coffee.py` - Coffee management endpoints
- `users.py` - User endpoints
- `admin.py` - Admin endpoints

### Telegram Bot (src/telegram/)
Telethon-based bot implementation lives under `src/api/telethon_api.py` and `src/bot/`.

## API Endpoints
- `GET /coffee/cards/` - Get active coffee cards
- `POST /coffee/cards/` - Create new coffee card
- `POST /coffee/orders/` - Create coffee order
- `GET /coffee/debts/user/{user_id}` - Get user debts
- `POST /coffee/payments/` - Record payment
- `GET /coffee/statistics/` - Get usage statistics

## Development Notes

### Working with Beanie Links
- Links require `fetch_link()` to access attributes
- Use document references in queries: `find(Model.field == document)`
- Don't access `link.attribute` directly - fetch first

### Testing
```bash
# Run tests
python -m pytest tests/

# Run specific test file
python -m pytest tests/test_coffee_handlers.py
```

### Common Tasks
- **Reset Database**: Drop collections or restart MongoDB
- **Add New Model**: Add to `coffee_models.py` and update handlers
- **New API Endpoint**: Add to appropriate router file
- **Bot Command**: Add to telegram handlers

## Troubleshooting

### Common Issues
1. **Import Errors**: Make sure virtual environment is activated
2. **Database Connection**: Check MongoDB is running and `MONGO_HOST` / `MONGO_PORT` are correct
3. **Telegram API**: Verify `API_ID`, `API_HASH`, and `BOT_TOKEN` in `.env`
4. **Link Attribute Errors**: Use `await document.fetch_link("field_name")` before accessing

### Debugging
```bash
# Start with debug logging
python -m src.main --log-level debug

# Check specific module
python -c "from src.models.coffee_models import CoffeeCard; print('OK')"
```
