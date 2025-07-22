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
pip install -r requirements.txt
```

### 3. Environment Variables
Create `.env` file with:
```
# Database
MONGODB_URL=mongodb://localhost:27017
DATABASE_NAME=coffee_bot

# Telegram Bot
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_BOT_TOKEN=your_bot_token

# Google Sheets (optional)
GOOGLE_SHEETS_CREDENTIALS_FILE=path_to_credentials.json
GOOGLE_SHEETS_SPREADSHEET_ID=your_spreadsheet_id

# Security
SECRET_KEY=your_secret_key
ALGORITHM=HS256
```

## Running the Application

### Development Server
```bash
# Make sure you're in the project root
cd "c:\Users\Lukas\git\coffee bot\telegram_coffee_bot_backend"

# Activate virtual environment
venv\Scripts\activate

# Start the FastAPI server
python -m src.main

# Alternative with uvicorn directly
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

### Database Setup
MongoDB should be running locally on port 27017, or update MONGODB_URL in .env

## Key Components

### Models (src/models/)
- `beanie_models.py` - User models (TelegramUser, FullUser)
- `coffee_models.py` - Coffee system models (CoffeeCard, CoffeeOrder, UserDebt, Payment)

### Handlers (src/handlers/)
- `coffee_handlers.py` - Business logic for coffee operations

### API Routes (src/routers/)
- `coffee.py` - Coffee management endpoints
- `auth.py` - Authentication endpoints

### Telegram Bot (src/telegram/)
- Telethon-based bot implementation

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
2. **Database Connection**: Check MongoDB is running and MONGODB_URL is correct
3. **Telegram API**: Verify API credentials in .env
4. **Link Attribute Errors**: Use `await document.fetch_link("field_name")` before accessing

### Debugging
```bash
# Start with debug logging
python -m src.main --log-level debug

# Check specific module
python -c "from src.models.coffee_models import CoffeeCard; print('OK')"
```
