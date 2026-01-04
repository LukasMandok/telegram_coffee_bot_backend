# Docker Deployment Guide

This repository supports two common ways of running the backend:

1. **Deployment compose (recommended for servers):** `docker-compose.deploy.yml`
  - Runs the published image from GitHub Container Registry (GHCR)
  - Includes `watchtower` for auto-updating the container
2. **Development compose / local runs:** `docker-compose.yml` (and local Python tooling)

## Building the Docker Image

### Locally
```bash
docker build -t telegram-coffee-bot-backend:latest .
```

### GitHub 
A prebuilt Docker image is available on GitHub Container Registry:

```bash
docker pull ghcr.io/lukasmandok/telegram_coffee_bot_backend:main
```

This is the preferred way to deploy the backend.

## Running the Container

### Option 0: Deployment compose (recommended)

This uses `docker-compose.deploy.yml` and runs the published image + watchtower.

```bash
docker compose -f docker-compose.deploy.yml pull
docker compose -f docker-compose.deploy.yml up -d

# Logs
docker compose -f docker-compose.deploy.yml logs -f coffee-bot
docker compose -f docker-compose.deploy.yml logs -f watchtower
```

### Option 1: Using docker-compose (Recommended)

1. Copy `.env.example` to `.env` and fill in your values:
```bash
cp .env.example .env
```

2. Start the container:
```bash
docker compose up
```

The docker-compose.yml automatically loads environment variables from your `.env` file.

### Option 2: Using docker run with --env-file

```bash
docker run -d \
  --name coffee-bot \
  --env-file .env \
  -p 8000:8000 \
  ghcr.io/lukasmandok/telegram_coffee_bot_backend:main
```

### Option 3: Passing environment variables individually

```bash
docker run -d \
  --name coffee-bot \
  -e BOT_TOKEN=your_token_here \
  -e API_ID=your_api_id \
  -e API_HASH=your_api_hash \
  -e BOT_HOST=your_host \
  -e DEFAULT_ADMIN=your_user_id \
  -p 8000:8000 \
  ghcr.io/lukasmandok/telegram_coffee_bot_backend:main
```

## Using the Published Image from GitHub Container Registry

Pull and run the latest image:

```bash
# Pull the image
docker pull ghcr.io/lukasmandok/telegram_coffee_bot_backend:main

# Run with your .env file
docker run -d \
  --name coffee-bot \
  --env-file .env \
  -p 8000:8000 \
  ghcr.io/lukasmandok/telegram_coffee_bot_backend:main
```

## Environment Variables

See `.env.example` for all required environment variables.

### Required Variables:
- `API_ID` - Telegram API ID
- `API_HASH` - Telegram API Hash
- `BOT_TOKEN` - Telegram Bot Token
- `BOT_HOST` - Bot Host URL
- `DEFAULT_ADMIN` - Default admin user ID

MongoDB connection is configured via:
- `MONGO_HOST`, `MONGO_PORT`
- `MONGO_INITDB_ROOT_USERNAME`, `MONGO_INITDB_ROOT_PASSWORD`, `MONGO_INITDB_DATABASE`

### Optional Variables:
- `DEBUG_MODE` - Enable debug mode (default: False)
- `LOG_LEVEL` - Logging level (default: INFO)
- `MONGO_INITDB_ROOT_USERNAME` - MongoDB root username
- `MONGO_INITDB_ROOT_PASSWORD` - MongoDB root password
- `GSHEET_SSID` - Google Spreadsheet ID
- `SERVICE_ACCOUNT_PRIVATE_KEY` - Google Service Account private key
- `SERVICE_ACCOUNT_EMAIL` - Google Service Account email

## Testing updates

To verify you can pull new images:

```bash
docker compose -f docker-compose.deploy.yml pull
docker compose -f docker-compose.deploy.yml up -d
docker compose -f docker-compose.deploy.yml images
```

To observe watchtower auto-updates:

```bash
docker compose -f docker-compose.deploy.yml logs -f watchtower
```
