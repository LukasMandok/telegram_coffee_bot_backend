# Telegram Coffee Tally Bot

Python Docker bot for managing a coffee list with MongoDB and optional Google Sheets integration.

## 🚀 Quick Start

One-liner setup (Linux/Mac/Git Bash):
```bash
curl -L https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/setup.sh | bash
```

Notes:
- The one-liner works for starting/updating the bot.
- The script *tries* to stay interactive even when piped (by reading from `/dev/tty`). If your environment has no TTY (e.g. some CI or remote shells), MongoDB setup prompts may be skipped.
- For the most reliable interactive flow (MongoDB prompts), download the script and run it locally.

Or Windows (PowerShell):
```powershell
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/setup.ps1" -OutFile "setup.ps1"; .\setup.ps1
```

### Interactive setup (recommended)

Linux/macOS:
```bash
curl -L -o setup.sh https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/setup.sh
chmod +x setup.sh
./setup.sh
```

Windows PowerShell:
```powershell
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/setup.ps1" -OutFile "setup.ps1"
.\setup.ps1
```

### Viewing logs
```bash
docker compose -f docker-compose.deploy.yml logs -f coffee-bot
```

## 📚 Documentation

- **[scripts/README.md](scripts/README.md)** - Setup scripts and quick start guide
- **[DOCKER.md](DOCKER.md)** - Docker deployment guide and advanced options
- **[DEVELOPMENT.md](DEVELOPMENT.md)** - Development setup and guidelines

---

## Optional: Google Sheets

Google Sheets integration is optional.

- Setup guide: `docs/google_sheets_setup.md`
- Variables: `.env.example`


## Docker deployment (high level)

Deployment uses `docker-compose.deploy.yml` which runs:
- `coffee-bot`: `ghcr.io/lukasmandok/telegram_coffee_bot_backend:main`
- `watchtower`: automatically checks for updated images and redeploys

To start/update manually:
```bash
docker compose -f docker-compose.deploy.yml pull
docker compose -f docker-compose.deploy.yml up -d
```

To watch auto-updates:
```bash
docker compose -f docker-compose.deploy.yml logs -f watchtower
```

### Setting up a Virtual Python Environment for  Development

To set up a virtual Python environment for development, follow these steps:

1. **Create a Virtual Environment**:
   Navigate to the backend directory and run:
   ```bash
   python -m venv venv
   ```
   This will create a virtual environment in a folder named `venv`.

2. **Activate the Virtual Environment**:
   - On Windows:
     ```bash
     .\venv\Scripts\activate
     ```
   - On macOS/Linux:
     ```bash
     source venv/bin/activate
     ```

3. **Install Dependencies**:
   Once the virtual environment is activated, install the required dependencies:
   ```bash
   pip install -r src/requirements.txt
   ```

4. **Run the Application**:
   You can now run the Python scripts directly for testing:
   ```bash
   python src/main.py
   ```

5. **Deactivate the Virtual Environment**:
   When you're done, deactivate the virtual environment by running:
   ```bash
   deactivate
   ```

#### Run unit test

1. **Virtual Eenvironmant