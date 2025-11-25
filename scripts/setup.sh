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

# Create and enter installation directory (unless in dev/git environment)
INSTALL_DIR="telegram_bot"
if [ -d ".git" ] || [ -f "docker-compose.yml" ] || [ -f "../docker-compose.yml" ]; then
    echo "ℹ️  Dev/Repo detected: Installing in current directory."
else
    if [ ! -d "$INSTALL_DIR" ]; then
        echo "📁 Creating installation directory: $INSTALL_DIR"
        mkdir -p "$INSTALL_DIR"
    fi
    echo "📂 Entering directory: $INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

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

    # Detect interactivity
    if [ -t 0 ]; then
        IS_INTERACTIVE=true
        INPUT_SOURCE="/dev/stdin"
    elif [ -c /dev/tty ]; then
        IS_INTERACTIVE=true
        INPUT_SOURCE="/dev/tty"
    else
        IS_INTERACTIVE=false
    fi

    if [ "$IS_INTERACTIVE" = true ]; then
        echo "Do you want to set up MongoDB in a Docker container with automated backups? (y/n)"
        echo "Note: If you already have MongoDB running, select 'n'"
        
        if [ "$INPUT_SOURCE" = "/dev/tty" ]; then
            echo -n "Setup MongoDB? [y/N]: "
            read -r setup_mongo < /dev/tty
        else
            read -r -p "Setup MongoDB? [y/N]: " setup_mongo
        fi
        setup_mongo=${setup_mongo:-n}
    else
        setup_mongo=${setup_mongo:-n}
        echo "Non-interactive mode detected (running via pipe?)."
        echo "   MongoDB setup is skipped by default because it requires user input."
        echo "   To set up MongoDB, please download the script and run it directly:"
        echo "     curl -L -o setup.sh https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/setup.sh"
        echo "     chmod +x setup.sh"
        echo "     ./setup.sh"
        echo ""
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
    if [ "$INPUT_SOURCE" = "/dev/tty" ]; then
        bash ./setup_mongodb_temp.sh < /dev/tty
    else
        bash ./setup_mongodb_temp.sh
    fi

        # If the child wrote the handshake file, read it and extract the DB URL
        if [ -f "$TMP_HANDSHAKE" ]; then
            CLEAN_OUTPUT=$(tr -d '\r' <"$TMP_HANDSHAKE" | sed -r 's/\x1B\[[0-9;]*[JKmsu]//g' || true)
            DB_URL=$(echo "$CLEAN_OUTPUT" | grep -a "MONGODB_CONNECTION=" | cut -d'=' -f2- || true)
            
            MONGO_USER=$(echo "$CLEAN_OUTPUT" | grep -a "MONGO_USERNAME=" | cut -d'=' -f2- || true)
            MONGO_PASS=$(echo "$CLEAN_OUTPUT" | grep -a "MONGO_PASSWORD=" | cut -d'=' -f2- || true)
            MONGO_DB=$(echo "$CLEAN_OUTPUT" | grep -a "MONGO_DATABASE=" | cut -d'=' -f2- || true)
            MONGO_PORT=$(echo "$CLEAN_OUTPUT" | grep -a "MONGO_PORT=" | cut -d'=' -f2- || true)

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
    
    # Set the MongoDB variables in .env
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        if [ -n "$MONGO_USER" ]; then sed -i '' "s|MONGO_INITDB_ROOT_USERNAME=.*|MONGO_INITDB_ROOT_USERNAME=$MONGO_USER|g" .env; fi
        if [ -n "$MONGO_PASS" ]; then sed -i '' "s|MONGO_INITDB_ROOT_PASSWORD=.*|MONGO_INITDB_ROOT_PASSWORD=$MONGO_PASS|g" .env; fi
        if [ -n "$MONGO_DB" ]; then sed -i '' "s|MONGO_INITDB_DATABASE=.*|MONGO_INITDB_DATABASE=$MONGO_DB|g" .env; fi
        if [ -n "$MONGO_PORT" ]; then sed -i '' "s|MONGO_PORT=.*|MONGO_PORT=$MONGO_PORT|g" .env; fi
        # Comment out DATABASE_URL if it exists
        sed -i '' "s|^DATABASE_URL=|# DATABASE_URL=|g" .env
    else
        # Linux
        if [ -n "$MONGO_USER" ]; then sed -i "s|MONGO_INITDB_ROOT_USERNAME=.*|MONGO_INITDB_ROOT_USERNAME=$MONGO_USER|g" .env; fi
        if [ -n "$MONGO_PASS" ]; then sed -i "s|MONGO_INITDB_ROOT_PASSWORD=.*|MONGO_INITDB_ROOT_PASSWORD=$MONGO_PASS|g" .env; fi
        if [ -n "$MONGO_DB" ]; then sed -i "s|MONGO_INITDB_DATABASE=.*|MONGO_INITDB_DATABASE=$MONGO_DB|g" .env; fi
        if [ -n "$MONGO_PORT" ]; then sed -i "s|MONGO_PORT=.*|MONGO_PORT=$MONGO_PORT|g" .env; fi
        # Comment out DATABASE_URL if it exists
        sed -i "s|^DATABASE_URL=|# DATABASE_URL=|g" .env
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
    echo "The MongoDB configuration has been updated."
    echo ""
    
    # Try to open .env in a default editor: use 'code' (VS Code) if available, then 'nano', then 'vim'
    if command -v code &> /dev/null; then
        if [ "$INPUT_SOURCE" = "/dev/tty" ]; then
            echo -n "Press Enter to edit .env with Visual Studio Code (code), or Ctrl+C to edit manually..."
            read -r _ < /dev/tty
        else
            read -p "Press Enter to edit .env with Visual Studio Code (code), or Ctrl+C to edit manually..."
        fi
        code --wait .env
    elif command -v nano &> /dev/null; then
        if [ "$INPUT_SOURCE" = "/dev/tty" ]; then
            echo -n "Press Enter to edit .env with nano, or Ctrl+C to edit manually..."
            read -r _ < /dev/tty
            nano .env < /dev/tty
        else
            read -p "Press Enter to edit .env with nano, or Ctrl+C to edit manually..."
            nano .env
        fi
    elif command -v vim &> /dev/null; then
        if [ "$INPUT_SOURCE" = "/dev/tty" ]; then
            echo -n "Press Enter to edit .env with vim, or Ctrl+C to edit manually..."
            read -r _ < /dev/tty
            vim .env < /dev/tty
        else
            read -p "Press Enter to edit .env with vim, or Ctrl+C to edit manually..."
            vim .env
        fi
    else
        if [ "$INPUT_SOURCE" = "/dev/tty" ]; then
            echo -n "Press Enter after you've edited the .env file..."
            read -r _ < /dev/tty
        else
            read -p "Press Enter after you've edited the .env file..."
        fi
    fi
else
    echo "✅ .env file already exists"
    # If a MongoDB setup was performed, ask to update variables in existing .env
    if [[ "$setup_mongo" =~ ^[Yy]$ ]]; then
        if [ "$INPUT_SOURCE" = "/dev/tty" ]; then
            echo -n "Do you want to update MongoDB variables in the existing .env? [Y/n]: "
            read -r update_db < /dev/tty
        else
            read -p "Do you want to update MongoDB variables in the existing .env? [Y/n]: " update_db
        fi
        update_db=${update_db:-Y}
        if [[ "$update_db" =~ ^[Yy]$ ]]; then
            if [[ "$OSTYPE" == "darwin"* ]]; then
                if [ -n "$MONGO_USER" ]; then sed -i '' "s|MONGO_INITDB_ROOT_USERNAME=.*|MONGO_INITDB_ROOT_USERNAME=$MONGO_USER|g" .env; fi
                if [ -n "$MONGO_PASS" ]; then sed -i '' "s|MONGO_INITDB_ROOT_PASSWORD=.*|MONGO_INITDB_ROOT_PASSWORD=$MONGO_PASS|g" .env; fi
                if [ -n "$MONGO_DB" ]; then sed -i '' "s|MONGO_INITDB_DATABASE=.*|MONGO_INITDB_DATABASE=$MONGO_DB|g" .env; fi
                if [ -n "$MONGO_PORT" ]; then sed -i '' "s|MONGO_PORT=.*|MONGO_PORT=$MONGO_PORT|g" .env; fi
                sed -i '' "s|^DATABASE_URL=|# DATABASE_URL=|g" .env
            else
                if [ -n "$MONGO_USER" ]; then sed -i "s|MONGO_INITDB_ROOT_USERNAME=.*|MONGO_INITDB_ROOT_USERNAME=$MONGO_USER|g" .env; fi
                if [ -n "$MONGO_PASS" ]; then sed -i "s|MONGO_INITDB_ROOT_PASSWORD=.*|MONGO_INITDB_ROOT_PASSWORD=$MONGO_PASS|g" .env; fi
                if [ -n "$MONGO_DB" ]; then sed -i "s|MONGO_INITDB_DATABASE=.*|MONGO_INITDB_DATABASE=$MONGO_DB|g" .env; fi
                if [ -n "$MONGO_PORT" ]; then sed -i "s|MONGO_PORT=.*|MONGO_PORT=$MONGO_PORT|g" .env; fi
                sed -i "s|^DATABASE_URL=|# DATABASE_URL=|g" .env
            fi
            echo "✅ MongoDB variables updated in .env"
        fi
    fi
    # Prompt user to edit existing .env
    if [ "$INPUT_SOURCE" = "/dev/tty" ]; then
        echo -n "Do you want to edit .env now? [y/N]: "
        read -r edit_env < /dev/tty
    else
        read -p "Do you want to edit .env now? [y/N]: " edit_env
    fi
    edit_env=${edit_env:-n}
    if [[ "$edit_env" =~ ^[Yy]$ ]]; then
        # Try to open with an available editor; prefer code, then nano, then vim
        if command -v code &> /dev/null; then
            code --wait .env
        elif command -v nano &> /dev/null; then
            if [ "$INPUT_SOURCE" = "/dev/tty" ]; then nano .env < /dev/tty; else nano .env; fi
        elif command -v vim &> /dev/null; then
            if [ "$INPUT_SOURCE" = "/dev/tty" ]; then vim .env < /dev/tty; else vim .env; fi
        else
            echo "No editor found (code/nano/vim). Please edit .env manually: ${PWD}/.env"
            if [ "$INPUT_SOURCE" = "/dev/tty" ]; then
                echo -n "Press Enter after you've edited the .env file..."
                read -r _ < /dev/tty
            else
                read -p "Press Enter after you've edited the .env file..."
            fi
        fi
    fi
fi

echo ""
echo "🐳 Pulling latest Docker image from GitHub Container Registry..."
# docker pull ghcr.io/lukasmandok/telegram_coffee_bot_backend:main

# Download docker-compose.deploy.yml
echo "📥 Downloading docker-compose.deploy.yml..."
curl -fsSL -o docker-compose.deploy.yml https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/docker-compose.deploy.yml

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
        if [ "$INPUT_SOURCE" = "/dev/tty" ]; then
            echo -n "Trim whitespace in .env keys/values (save backup)? [Y/n]: "
            read -r apply_fix < /dev/tty
        else
            read -p "Trim whitespace in .env keys/values (save backup)? [Y/n]: " apply_fix
        fi
        apply_fix=${apply_fix:-Y}
        if [[ "$apply_fix" =~ ^[Yy]$ ]]; then cp .env .env.bak && mv $TMP_ENV .env && echo "✅ .env sanitized (backup .env.bak)"; else rm -f $TMP_ENV; fi
    else rm -f $TMP_ENV; echo "✅ .env validated"; fi
fi

# Stop and remove existing container if it exists (cleanup old manual runs)
docker stop telegram-coffee-bot 2>/dev/null || true
docker rm telegram-coffee-bot 2>/dev/null || true

# Check for docker compose command
if docker compose version &>/dev/null; then
    DOCKER_COMPOSE_CMD="docker compose"
elif command -v docker-compose &>/dev/null; then
    DOCKER_COMPOSE_CMD="docker-compose"
else
    echo "❌ Error: docker-compose is not installed."
    echo "Please install docker-compose or update Docker Desktop."
    exit 1
fi

echo "Using $DOCKER_COMPOSE_CMD..."
$DOCKER_COMPOSE_CMD -f docker-compose.deploy.yml pull
$DOCKER_COMPOSE_CMD -f docker-compose.deploy.yml up -d

echo ""
echo "=========================================="
echo "✅ Bot is now running with Watchtower (Auto-Updates)!"
echo "=========================================="
echo ""
echo "Container name: telegram_bot-coffee-bot-1 (or similar)"
if [[ "$setup_mongo" =~ ^[Yy]$ ]]; then
    echo "MongoDB container: telegram-coffee-mongodb"
fi
echo "API endpoint: http://localhost:8000"
echo ""
echo "Useful commands:"
echo "  View bot logs:       $DOCKER_COMPOSE_CMD -f docker-compose.deploy.yml logs -f coffee-bot"
echo "  Stop bot:            $DOCKER_COMPOSE_CMD -f docker-compose.deploy.yml down"
echo "  Restart bot:         $DOCKER_COMPOSE_CMD -f docker-compose.deploy.yml restart coffee-bot"
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
