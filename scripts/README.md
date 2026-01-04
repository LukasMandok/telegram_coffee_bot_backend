# Setup Scripts

This directory contains all setup and management scripts for the Telegram Coffee Bot.

## Main Setup Scripts

### setup.sh / setup.ps1
Main installation script that downloads and runs the pre-built Docker image.

**Features:**
- Downloads `docker-compose.deploy.yml` from GitHub
- Pulls the latest Docker image from GitHub Container Registry (GHCR)
- Optional MongoDB setup with automated backups
- Creates and configures `.env` file
- Starts the bot container

**How updates work:**
- **At install time:** the script runs `docker compose -f docker-compose.deploy.yml pull`.
- **Afterwards (auto-update):** `docker-compose.deploy.yml` includes `watchtower`, which periodically checks for newer images and redeploys.

**Usage:**
```bash
# Linux/Mac/Git Bash
curl -L -o setup.sh https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/setup.sh
bash setup.sh

# Windows PowerShell
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/setup.ps1" -OutFile "setup.ps1"
.\setup.ps1
```

### One-liner vs interactive mode

When you run `curl ... | bash`, stdin is often not interactive.
The setup script still:
- downloads `docker-compose.deploy.yml`
- runs `docker compose pull` + `up -d`

For prompts (MongoDB setup, editing `.env`), the script attempts to read from `/dev/tty` so it can still be interactive even when piped.
If your environment provides no TTY (some CI/remote shells), prompts may be skipped.

If you want the interactive MongoDB setup, download the script and run it locally (not piped):

```bash
curl -L -o setup.sh https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/setup.sh
chmod +x setup.sh
./setup.sh
```

### setup_mongodb.sh / setup_mongodb.ps1
MongoDB setup script with backup automation.

**Features:**
- Interactive configuration for MongoDB and backups
- Creates MongoDB container with persistent storage
- Downloads and configures backup/restore scripts
- Sets up automated daily backups (cron/Task Scheduler)
- Creates documentation

**Note:** This script is automatically downloaded and run by the main setup script if you choose MongoDB setup.

## MongoDB Management Scripts

### mongo_backup.sh / mongo_backup.ps1
Performs full MongoDB backups with automatic rotation.

**Features:**
- Creates compressed backup archives
- Automatically deletes old backups based on retention policy
- Logs backup operations

**Configuration:** Downloaded and configured by `setup_mongodb` script with your settings.

### mongo_restore.sh / mongo_restore.ps1
Restores MongoDB from backup archives.

**Features:**
- Lists available backups
- Confirmation prompt before restoring
- Drops existing data before restore

**Configuration:** Downloaded and configured by `setup_mongodb` script with your settings.

## Documentation

### MONGODB_README.md
Comprehensive documentation for MongoDB management.

**Contains:**
- Configuration details
- Script usage examples
- Useful commands
- Connection strings
- Troubleshooting tips

**Configuration:** Downloaded and configured by `setup_mongodb` script with your settings.

### Google Sheets (optional)

If you want Google Sheets backup, see:
- `docs/google_sheets_setup.md`

## Quick Start

**Option 1: One-line setup (Linux/Mac/Git Bash)**
```bash
curl -L https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/setup.sh | bash
```

**Option 2: One-line setup (Windows PowerShell)**
```powershell
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/setup.ps1" -OutFile "setup.ps1"; .\setup.ps1
```

**Option 3: Download and run**
```bash
# Download setup script
curl -L -o setup.sh https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/setup.sh

# Make executable (Linux/Mac)
chmod +x setup.sh

# Run
bash setup.sh  # Linux/Mac
.\setup.ps1    # Windows
```

## Script Architecture

All scripts use a template-based approach with placeholders:
- Templates are stored in this repository (`scripts/` folder)
- Setup scripts download templates from GitHub
- Placeholders (e.g., `{{MONGO_USERNAME}}`) are replaced with user configuration
- Configured scripts are placed in user's chosen directories

**Benefits:**
- Always get latest versions
- Easy to maintain and update
- Single source of truth
- No need to keep multiple files in sync

## Notes

- All setup scripts can be run directly from GitHub without cloning the repository
- MongoDB scripts are automatically configured during setup
- Backup scripts respect your configured retention period
- All scripts include error handling and user-friendly messages

## Useful commands (after setup)

```bash
# Bot logs
docker compose -f docker-compose.deploy.yml logs -f coffee-bot

# Watchtower logs (auto-update)
docker compose -f docker-compose.deploy.yml logs -f watchtower

# Manual update
docker compose -f docker-compose.deploy.yml pull
docker compose -f docker-compose.deploy.yml up -d
```
