#!/bin/bash
# Telegram Coffee Bot - Quick Setup Script
# This script downloads the latest Docker image and sets up the environment

set -e
# Disable history expansion to avoid '!' being interpreted
set +H

echo "=========================================="
echo "Telegram Coffee Bot - Quick Setup"
echo "=========================================="
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Error: Docker is not installed."
    echo "Please install Docker from: https://docs.docker.com/get-docker/"
    exit 1
fi
## Check that Docker can be contacted (is running)
if ! docker info &> /dev/null; then
    echo "❌ Error: Docker is not running."
    echo "Please start Docker and try again."
    exit 1
fi
echo "✅ Docker is installed and running"
echo ""

# Check required CLI dependencies early and notify user
required=(curl sed grep cut tr xargs mktemp)
optional=(code nano vim crontab)
missing=()
for c in "${required[@]}"; do
    if ! command -v "$c" &>/dev/null; then
        missing+=("$c")
    fi
done
if [ ${#missing[@]} -gt 0 ]; then
    echo "❌ Missing required programs: ${missing[*]}"
    echo "Please install them and re-run the setup. Examples for Debian/Ubuntu:" 
    echo "  sudo apt update && sudo apt install -y ${missing[*]}"
    exit 1
fi
warn=()
for c in "${optional[@]}"; do
    if ! command -v "$c" &>/dev/null; then
        warn+=("$c")
    fi
done
if [ ${#warn[@]} -gt 0 ]; then
    echo "⚠️ Optional programs not found: ${warn[*]}" 
    echo "  (Editors or cron utilities). The setup will continue, but you may want to install them."
fi
    # Ask about MongoDB setup (interactive only)
    # Allow environment override (SETUP_MONGO or setup_mongo)
    if [ -n "${SETUP_MONGO:-}" ]; then
        setup_mongo="${SETUP_MONGO}"
    elif [ -n "${setup_mongo:-}" ]; then
        setup_mongo="${setup_mongo}"
    fi
    if [ -t 0 ]; then
        echo "Do you want to set up MongoDB in a Docker container with automated backups? (y/n)"
        echo "Note: If you already have MongoDB running, select 'n'"
        read -r -p "Setup MongoDB? [y/N]: " setup_mongo
        setup_mongo=${setup_mongo:-n}
    else
        setup_mongo=${setup_mongo:-n}
        echo "Non-interactive: MongoDB setup will be skipped by default (set setup_mongo=y to override)"
    fi

    if [[ "$setup_mongo" =~ ^[Yy]$ ]]; then
    echo "📥 Downloading MongoDB setup script..."
        # Check if local setup_mongodb.sh exists (dev mode)
        SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        if [ -f "$SCRIPT_DIR/setup_mongodb.sh" ]; then
            echo "   Using local setup_mongodb.sh"
            cp "$SCRIPT_DIR/setup_mongodb.sh" setup_mongodb_temp.sh
        elif [ -f "./setup_mongodb.sh" ]; then
            echo "   Using local ./setup_mongodb.sh"
            cp "./setup_mongodb.sh" setup_mongodb_temp.sh
        else
            # MongoDB child script (`setup_mongodb.sh`) will handle existing-instance detection and prompts
            curl -fsSL -o setup_mongodb_temp.sh https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/setup_mongodb.sh
        fi
        chmod +x setup_mongodb_temp.sh

        # Run the MongoDB setup script interactively so users can see prompts.
        # To extract the resulting connection string in a deterministic manner,
        # pass a temp handshake file path via MONGO_HANDSHAKE_FILE where the
        # child script can write the final MONGODB_CONNECTION line.
        TMP_HANDSHAKE=$(mktemp)
        export MONGO_HANDSHAKE_FILE="$TMP_HANDSHAKE"
    echo "↪ Running MongoDB setup script now. This script is interactive and will prompt for credentials and options."
    bash ./setup_mongodb_temp.sh

        # If the child wrote the handshake file, read it and extract the DB URL
        if [ -f "$TMP_HANDSHAKE" ]; then
            CLEAN_OUTPUT=$(tr -d '\r' <"$TMP_HANDSHAKE" | sed -r 's/\x1B\[[0-9;]*[JKmsu]//g' || true)
            DB_URL=$(echo "$CLEAN_OUTPUT" | grep -a "MONGODB_CONNECTION=" | cut -d'=' -f2- || true)
            rm -f "$TMP_HANDSHAKE"
        fi
        # Escape DB_URL for safe insertion into .env (escape / and &)
        if [ -n "$DB_URL" ]; then
            DB_URL_ESCAPED=$(printf '%s' "$DB_URL" | sed -e 's/[\/&]/\\&/g')
        fi
        rm -f setup_mongodb_temp.sh
        if [ -z "$DB_URL" ]; then
            DB_URL="mongodb://admin:password123@localhost:27017/telegram_bot"
            echo "⚠️  Couldn't extract MONGODB connection string from MongoDB setup output."
            echo "   Here is the raw output for debugging (first 200 chars):"
            echo "$(printf '%s' "$CLEAN_OUTPUT" | cut -c1-200)"
        fi
    else
        echo "⏭️  Skipping MongoDB setup"
        echo ""
        DB_URL="mongodb://admin:password123@localhost:27017/telegram_bot"
    fi

# Always ensure DB_URL_ESCAPED is set (for sed replacement into .env)
if [ -n "$DB_URL" ]; then
    DB_URL_ESCAPED=$(printf '%s' "$DB_URL" | sed -e 's/[\/&]/\\&/g')
    echo "Detected MongoDB connection: $DB_URL"
else
    DB_URL_ESCAPED=""
fi

# Download .env.example if it doesn't exist
if [ ! -f ".env" ]; then
    echo "📥 Downloading .env.example template..."
    curl -fsSL -o .env.example https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/.env.example
    
    echo "📝 Creating .env file from template..."
    cp .env.example .env
    
    # Set the DATABASE_URL in .env
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
            sed -i '' "s|DATABASE_URL=.*|DATABASE_URL=$DB_URL_ESCAPED|g" .env
    else
        # Linux
            sed -i "s|DATABASE_URL=.*|DATABASE_URL=$DB_URL_ESCAPED|g" .env
    fi
    
    echo ""
    echo "⚠️  IMPORTANT: You need to edit the .env file with your credentials!"
    echo ""
    echo "Required variables:"
    echo "  - API_ID: Your Telegram API ID (get from https://my.telegram.org)"
    echo "  - API_HASH: Your Telegram API Hash"
    echo "  - BOT_TOKEN: Your Telegram Bot Token (get from @BotFather)"
    echo "  - BOT_HOST: Your bot host URL"
    echo "  - DEFAULT_ADMIN: Your Telegram user ID"
    echo ""
    echo "The DATABASE_URL has been set to: $DB_URL"
    echo ""
    
    # Try to open .env in a default editor: use 'code' (VS Code) if available, then 'nano', then 'vim'
    if command -v code &> /dev/null; then
        read -p "Press Enter to edit .env with Visual Studio Code (code), or Ctrl+C to edit manually..."
        code --wait .env
    elif command -v nano &> /dev/null; then
        read -p "Press Enter to edit .env with nano, or Ctrl+C to edit manually..."
        nano .env
    elif command -v vim &> /dev/null; then
        read -p "Press Enter to edit .env with vim, or Ctrl+C to edit manually..."
        vim .env
    else
        read -p "Press Enter after you've edited the .env file..."
    fi
else
    echo "✅ .env file already exists"
    # If a MongoDB setup was performed, ask to update DATABASE_URL in existing .env
    if [[ "$setup_mongo" =~ ^[Yy]$ ]]; then
        read -p "Do you want to update DATABASE_URL in the existing .env to the generated MongoDB connection? [Y/n]: " update_dburl
        update_dburl=${update_dburl:-Y}
        if [[ "$update_dburl" =~ ^[Yy]$ ]]; then
            if [[ "$OSTYPE" == "darwin"* ]]; then
                sed -i '' "s|DATABASE_URL=.*|DATABASE_URL=$DB_URL_ESCAPED|g" .env
            else
                sed -i "s|DATABASE_URL=.*|DATABASE_URL=$DB_URL_ESCAPED|g" .env
            fi
            echo "✅ DATABASE_URL updated in .env"
        fi
    fi
    # Prompt user to edit existing .env
    read -p "Do you want to edit .env now? [y/N]: " edit_env
    edit_env=${edit_env:-n}
    if [[ "$edit_env" =~ ^[Yy]$ ]]; then
        # Try to open with an available editor; prefer code, then nano, then vim
        if command -v code &> /dev/null; then
            code --wait .env
        elif command -v nano &> /dev/null; then
            nano .env
        elif command -v vim &> /dev/null; then
            vim .env
        else
            echo "No editor found (code/nano/vim). Please edit .env manually: ${PWD}/.env"
            read -p "Press Enter after you've edited the .env file..."
        fi
    fi
fi

echo ""
echo "🐳 Pulling latest Docker image from GitHub Container Registry..."
docker pull ghcr.io/lukasmandok/telegram_coffee_bot_backend:main

echo ""
echo "🚀 Starting the bot..."

# Validate/sanitize .env (trim whitespace, reject keys with spaces)
if [ -f ".env" ]; then
    TMP_ENV=$(mktemp)
    CHANGED=0; INVALID_KEYS=()
    while IFS= read -r LINE || [ -n "$LINE" ]; do
        case "$LINE" in
            ''|'#'*) printf '%s\n' "$LINE" >>$TMP_ENV; continue;;
            *'='*) K=${LINE%%=*}; V=${LINE#*=}; K2=$(echo "$K" | xargs); V2=$(echo "$V" | xargs); if [[ "$K2" != "$K" || "$V2" != "$V" ]]; then CHANGED=1; fi; if [[ "$K2" =~ [[:space:]] ]]; then INVALID_KEYS+=("$K2"); fi; printf '%s=%s\n' "$K2" "$V2" >>$TMP_ENV; continue;;
            *) printf '%s\n' "$LINE" >>$TMP_ENV; continue;;
        esac
    done <.env
    if [ ${#INVALID_KEYS[@]} -gt 0 ]; then
        echo "❌ Invalid .env keys contain whitespace: ${INVALID_KEYS[*]}. Docker will reject them."
        rm -f $TMP_ENV; exit 1
    fi
    if [ $CHANGED -eq 1 ]; then
        read -p "Trim whitespace in .env keys/values (save backup)? [Y/n]: " apply_fix; apply_fix=${apply_fix:-Y}
        if [[ "$apply_fix" =~ ^[Yy]$ ]]; then cp .env .env.bak && mv $TMP_ENV .env && echo "✅ .env sanitized (backup .env.bak)"; else rm -f $TMP_ENV; fi
    else rm -f $TMP_ENV; echo "✅ .env validated"; fi
fi

# Stop and remove existing container if it exists
docker stop telegram-coffee-bot 2>/dev/null || true
docker rm telegram-coffee-bot 2>/dev/null || true

docker run -d \
  --name telegram-coffee-bot \
  --env-file .env \
  --restart unless-stopped \
  --network host \
  -v "$(pwd)/src:/app/src" \
  ghcr.io/lukasmandok/telegram_coffee_bot_backend:main

echo ""
echo "=========================================="
echo "✅ Bot is now running!"
echo "=========================================="
echo ""
echo "Container name: telegram-coffee-bot"
if [[ "$setup_mongo" =~ ^[Yy]$ ]]; then
    echo "MongoDB container: telegram-coffee-mongodb"
fi
echo "API endpoint: http://localhost:8000"
echo ""
echo "Useful commands:"
echo "  View bot logs:       docker logs telegram-coffee-bot"
echo "  Follow bot logs:     docker logs -f telegram-coffee-bot"
echo "  Stop bot:            docker stop telegram-coffee-bot"
echo "  Start bot:           docker start telegram-coffee-bot"
echo "  Restart bot:         docker restart telegram-coffee-bot"
echo "  Remove bot:          docker rm -f telegram-coffee-bot"
if [[ "$setup_mongo" =~ ^[Yy]$ ]]; then
    echo ""
    echo "MongoDB commands:"
    echo "  View MongoDB logs:   docker logs telegram-coffee-mongodb"
    echo "  Stop MongoDB:        docker stop telegram-coffee-mongodb"
    echo "  Start MongoDB:       docker start telegram-coffee-mongodb"
    echo "  Remove MongoDB:      docker rm -f telegram-coffee-mongodb"
    echo "  Remove MongoDB data: docker volume rm telegram-coffee-mongodb-data"
fi
echo ""
