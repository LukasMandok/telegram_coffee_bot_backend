#!/bin/bash
# Telegram Coffee Bot - Quick Setup Script
# This script downloads the latest Docker image and sets up the environment

set -e

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

# Check if Docker is running
if ! docker info &> /dev/null; then
    echo "❌ Error: Docker is not running."
    echo "Please start Docker and try again."
    exit 1
fi

echo "✅ Docker is installed and running"
echo ""

# Ask about MongoDB setup
echo "Do you want to set up MongoDB in a Docker container with automated backups? (y/n)"
echo "Note: If you already have MongoDB running, select 'n'"
read -p "Setup MongoDB? [y/N]: " setup_mongo
setup_mongo=${setup_mongo:-n}

if [[ "$setup_mongo" =~ ^[Yy]$ ]]; then
    echo ""
    echo "📥 Downloading MongoDB setup script..."
    
    # Download setup_mongodb.sh from GitHub
    curl -L -o setup_mongodb_temp.sh https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/setup_mongodb.sh
    chmod +x setup_mongodb_temp.sh
    
    # Run MongoDB setup script and capture output
    MONGO_OUTPUT=$(bash ./setup_mongodb_temp.sh)
    
    # Extract connection string from output
    DB_URL=$(echo "$MONGO_OUTPUT" | grep "MONGODB_CONNECTION=" | cut -d'=' -f2-)
    
    # Clean up temporary script
    rm -f setup_mongodb_temp.sh
    
    if [ -z "$DB_URL" ]; then
        # Fallback to default if connection string not found
        DB_URL="mongodb://admin:password123@localhost:27017/fastapi"
    fi
else
    echo "⏭️  Skipping MongoDB setup"
    echo ""
    DB_URL="mongodb://admin:password123@localhost:27017/fastapi"
fi

# Download .env.example if it doesn't exist
if [ ! -f ".env" ]; then
    echo "📥 Downloading .env.example template..."
    curl -L -o .env.example https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/.env.example
    
    echo "📝 Creating .env file from template..."
    cp .env.example .env
    
    # Set the DATABASE_URL in .env
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        sed -i '' "s|DATABASE_URL=.*|DATABASE_URL=$DB_URL|g" .env
    else
        # Linux
        sed -i "s|DATABASE_URL=.*|DATABASE_URL=$DB_URL|g" .env
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
    
    # Try to open .env in default editor
    if command -v nano &> /dev/null; then
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
fi

echo ""
echo "🐳 Pulling latest Docker image from GitHub Container Registry..."
docker pull ghcr.io/lukasmandok/telegram_coffee_bot_backend:main

echo ""
echo "🚀 Starting the bot..."

# Stop and remove existing container if it exists
docker stop telegram-coffee-bot 2>/dev/null || true
docker rm telegram-coffee-bot 2>/dev/null || true

docker run -d \
  --name telegram-coffee-bot \
  --env-file .env \
  --restart unless-stopped \
  --network host \
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
