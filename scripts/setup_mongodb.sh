#!/bin/bash
# MongoDB Setup Script with Backup Configuration
# This script sets up MongoDB in Docker with automated backups

# Make script fail fast, yet disable interactive history expansion within this script
set -e
set +H

echo "=========================================="
echo "MongoDB Setup"
echo "=========================================="
echo ""

# Default values
DEFAULT_BACKUP_DIR="$HOME/mongodb-backups"
DEFAULT_SCRIPTS_DIR="$HOME/scripts"
DEFAULT_RETENTION_DAYS=30
DEFAULT_BACKUP_TIME="03:00"
DEFAULT_USERNAME="admin"
DEFAULT_PASSWORD="password123"
DEFAULT_DATABASE="telegram_bot"
DEFAULT_PORT=27017

## Detect existing MongoDB instance
EXISTING_CONTAINER=""
PORT_OPEN=false

# 1) Exact container name (telegram-coffee-mongodb)
if docker ps -a --format '{{.Names}}' | grep -q '^telegram-coffee-mongodb$'; then
    EXISTING_CONTAINER="telegram-coffee-mongodb"
fi

# 2) Any container with "mongo" in the name (e.g., my-mongo, mongodb-service)
if [ -z "$EXISTING_CONTAINER" ]; then
    if docker ps -a --format '{{.Names}}' | grep -i -q 'mongo'; then
        EXISTING_CONTAINER=$(docker ps -a --format '{{.Names}}' | grep -i 'mongo' | head -n1)
    fi
fi

# 3) Any container whose image suggests MongoDB (images like 'mongo', 'bitnami/mongodb', etc.)
if [ -z "$EXISTING_CONTAINER" ]; then
    if docker ps -a --format '{{.Names}} {{.Image}}' | grep -Ei 'mongo($|:|\/|bitnami)' &>/dev/null; then
        EXISTING_CONTAINER=$(docker ps -a --format '{{.Names}} {{.Image}}' | grep -Ei 'mongo($|:|\/|bitnami)' | head -n1 | awk '{print $1}')
    fi
fi

# 4) Containers that have a host port mapping to 27017
if [ -z "$EXISTING_CONTAINER" ]; then
    CONTAINER_WITH_PORT=$(docker ps --format '{{.Names}} {{.Ports}}' | grep -E ':27017' | head -n1 | awk '{print $1}' || true)
    if [ -n "$CONTAINER_WITH_PORT" ]; then
        EXISTING_CONTAINER="$CONTAINER_WITH_PORT"
    fi
fi

# 5) Host port 27017 open detection: ss, netstat, or /dev/tcp fallback
if command -v ss &>/dev/null; then
    if ss -ltn | grep -q ':27017'; then PORT_OPEN=true; fi
elif command -v netstat &>/dev/null; then
    if netstat -ltn | grep -q ':27017'; then PORT_OPEN=true; fi
else
    # fallback: try to connect with bash /dev/tcp if the shell supports it
    if timeout 1 bash -c "</dev/tcp/127.0.0.1/27017" &>/dev/null; then PORT_OPEN=true; fi
fi

if [ -n "$EXISTING_CONTAINER" ] || [ "$PORT_OPEN" = true ]; then
    echo "⚠️ Detected existing MongoDB instance:"
    [[ -n "$EXISTING_CONTAINER" ]] && echo "  - Docker container: $EXISTING_CONTAINER (image: $(docker inspect --format '{{.Config.Image}}' $EXISTING_CONTAINER 2>/dev/null || true), ports: $(docker ps --format '{{.Names}} {{.Ports}}' | grep "^$EXISTING_CONTAINER" || true))"
    [[ "$PORT_OPEN" == true ]] && echo "  - TCP port 27017 is open on this host"
    read -p "Options: [S]kip use existing (default), [R]ecreate (delete container), [C]ontinue setup script (may overwrite): [S/r/c]: " mongo_action
    mongo_action=${mongo_action:-S}
    if [[ "$mongo_action" =~ ^[Ss]$ ]]; then
        # Try to extract connection string from existing container
        if [[ -n "$EXISTING_CONTAINER" ]]; then
            MONGO_USERNAME=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$EXISTING_CONTAINER" | grep '^MONGO_INITDB_ROOT_USERNAME=' | cut -d'=' -f2- || true)
            MONGO_PASSWORD=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$EXISTING_CONTAINER" | grep '^MONGO_INITDB_ROOT_PASSWORD=' | cut -d'=' -f2- || true)
            MONGO_DATABASE=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$EXISTING_CONTAINER" | grep '^MONGO_INITDB_DATABASE=' | cut -d'=' -f2- || true)
            MONGO_PORT=$(docker inspect --format '{{ if index .NetworkSettings.Ports "27017/tcp" }}{{ (index (index .NetworkSettings.Ports "27017/tcp") 0).HostPort }}{{ end }}' "$EXISTING_CONTAINER" || true)
        fi
        if [[ -z "$MONGO_PORT" ]]; then MONGO_PORT=27017; fi
        if [[ -n "$MONGO_USERNAME" && -n "$MONGO_PASSWORD" ]]; then
            echo "Using credentials from container"
            CONN_STR="MONGODB_CONNECTION=mongodb://$MONGO_USERNAME:$MONGO_PASSWORD@localhost:$MONGO_PORT/$MONGO_DATABASE?authSource=admin"
            echo "$CONN_STR"
            if [ -n "${MONGO_HANDSHAKE_FILE:-}" ]; then
                echo "$CONN_STR" > "$MONGO_HANDSHAKE_FILE" || true
            fi
            exit 0
        else
            echo "Could not extract credentials automatically. Please provide them to use the existing MongoDB instance." 
            # fall through to prompt for credentials below
        fi
    elif [[ "$mongo_action" =~ ^[Rr]$ ]]; then
        # Recreate: remove container and optionally remove volume
        target="$EXISTING_CONTAINER"
        read -p "Recreate container $target? This will remove it. Keep data volume? [Y/n]: " keep_vol
        keep_vol=${keep_vol:-Y}
        docker rm -f "$target" 2>/dev/null || true
        if [[ ! "$keep_vol" =~ ^[Yy]$ ]]; then
            docker volume rm telegram-coffee-mongodb-data 2>/dev/null || true
        fi
        echo "Continuing setup: recreating MongoDB container..."
    else
        echo "Continuing with MongoDB setup (interactive script)"
    fi
fi

## Ask for MongoDB credentials
read -p "MongoDB username [$DEFAULT_USERNAME]: " input_username
MONGO_USERNAME=${input_username:-$DEFAULT_USERNAME}

read -p "MongoDB password [$DEFAULT_PASSWORD]: " input_password
MONGO_PASSWORD=${input_password:-$DEFAULT_PASSWORD}

read -p "MongoDB database [$DEFAULT_DATABASE]: " input_database
MONGO_DATABASE=${input_database:-$DEFAULT_DATABASE}

read -p "MongoDB port [$DEFAULT_PORT]: " input_port
MONGO_PORT=${input_port:-$DEFAULT_PORT}

echo ""
# Ask for backup configuration
read -p "Backup directory [$DEFAULT_BACKUP_DIR]: " input_backup_dir
BACKUP_DIR=${input_backup_dir:-$DEFAULT_BACKUP_DIR}

read -p "Scripts directory [$DEFAULT_SCRIPTS_DIR]: " input_scripts_dir
SCRIPTS_DIR=${input_scripts_dir:-$DEFAULT_SCRIPTS_DIR}

read -p "Backup retention days [$DEFAULT_RETENTION_DAYS]: " input_retention
RETENTION_DAYS=${input_retention:-$DEFAULT_RETENTION_DAYS}

read -p "Backup time (HH:MM) [$DEFAULT_BACKUP_TIME]: " input_time
BACKUP_TIME=${input_time:-$DEFAULT_BACKUP_TIME}

# Parse backup time
IFS=':' read -r BACKUP_HOUR BACKUP_MINUTE <<< "$BACKUP_TIME"

echo ""
echo "🗄️  Setting up MongoDB container..."

# Stop and remove existing MongoDB container if it exists
docker stop telegram-coffee-mongodb 2>/dev/null || true
docker rm telegram-coffee-mongodb 2>/dev/null || true

# Create volume and start MongoDB container
docker volume create telegram-coffee-mongodb-data 2>/dev/null || true

docker run -d \
  --name telegram-coffee-mongodb \
  --restart unless-stopped \
  -p $MONGO_PORT:27017 \
  -e MONGO_INITDB_ROOT_USERNAME=$MONGO_USERNAME \
  -e MONGO_INITDB_ROOT_PASSWORD=$MONGO_PASSWORD \
  -e MONGO_INITDB_DATABASE=$MONGO_DATABASE \
  -v telegram-coffee-mongodb-data:/data/db \
  mongo:latest

echo "✅ MongoDB container started"

# Wait for MongoDB to start
sleep 5

echo ""
echo "📁 Setting up backup automation..."

# Create backup and scripts directories
mkdir -p "$BACKUP_DIR"
mkdir -p "$SCRIPTS_DIR"

# Download backup script from GitHub
BACKUP_SCRIPT="$SCRIPTS_DIR/mongo_backup.sh"
curl -fsSL -o "$BACKUP_SCRIPT" https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/mongo_backup.sh

# Replace placeholders in backup script
if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS
    sed -i '' "s|{{BACKUP_DIR}}|$BACKUP_DIR|g" "$BACKUP_SCRIPT"
    sed -i '' "s|{{MONGO_USERNAME}}|$MONGO_USERNAME|g" "$BACKUP_SCRIPT"
    sed -i '' "s|{{MONGO_PASSWORD}}|$MONGO_PASSWORD|g" "$BACKUP_SCRIPT"
    sed -i '' "s|{{MONGO_PORT}}|$MONGO_PORT|g" "$BACKUP_SCRIPT"
    sed -i '' "s|{{RETENTION_DAYS}}|$RETENTION_DAYS|g" "$BACKUP_SCRIPT"
else
    # Linux
    sed -i "s|{{BACKUP_DIR}}|$BACKUP_DIR|g" "$BACKUP_SCRIPT"
    sed -i "s|{{MONGO_USERNAME}}|$MONGO_USERNAME|g" "$BACKUP_SCRIPT"
    sed -i "s|{{MONGO_PASSWORD}}|$MONGO_PASSWORD|g" "$BACKUP_SCRIPT"
    sed -i "s|{{MONGO_PORT}}|$MONGO_PORT|g" "$BACKUP_SCRIPT"
    sed -i "s|{{RETENTION_DAYS}}|$RETENTION_DAYS|g" "$BACKUP_SCRIPT"
fi

# Make backup script executable
chmod +x "$BACKUP_SCRIPT"

# Test backup script
if bash "$BACKUP_SCRIPT" > /dev/null 2>&1; then
    echo "✅ Backup script created and tested"
else
    echo "⚠️  Backup script created (initial test failed, but will work after MongoDB is ready)"
fi

# Set up cron job
CRON_JOB="$BACKUP_HOUR $BACKUP_MINUTE * * * $BACKUP_SCRIPT >> $SCRIPTS_DIR/mongo_backup.log 2>&1"
CRON_COMMENT="# Telegram Coffee Bot MongoDB Backup"

# Set up cron job if available; otherwise show instructions
if command -v crontab &>/dev/null; then
    # Remove existing cron job if it exists
    crontab -l 2>/dev/null | grep -a -v "mongo_backup.sh" | grep -a -v "Telegram Coffee Bot MongoDB Backup" | crontab - 2>/dev/null || true
    # Add new cron job
    (crontab -l 2>/dev/null; echo "$CRON_COMMENT"; echo "$CRON_JOB") | crontab -
    echo "✅ Automated backup scheduled at $BACKUP_TIME daily"
else
    echo "⚠️  crontab not found on this host. Skipping automated cron setup."
    echo "  Add the following line to your crontab to enable backups:" 
    echo "    $CRON_JOB"
fi

# Download restore script from GitHub
RESTORE_SCRIPT="$SCRIPTS_DIR/mongo_restore.sh"
curl -fsSL -o "$RESTORE_SCRIPT" https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/mongo_restore.sh

# Replace placeholders in restore script
if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS
    sed -i '' "s|{{BACKUP_DIR}}|$BACKUP_DIR|g" "$RESTORE_SCRIPT"
    sed -i '' "s|{{MONGO_USERNAME}}|$MONGO_USERNAME|g" "$RESTORE_SCRIPT"
    sed -i '' "s|{{MONGO_PASSWORD}}|$MONGO_PASSWORD|g" "$RESTORE_SCRIPT"
else
    # Linux
    sed -i "s|{{BACKUP_DIR}}|$BACKUP_DIR|g" "$RESTORE_SCRIPT"
    sed -i "s|{{MONGO_USERNAME}}|$MONGO_USERNAME|g" "$RESTORE_SCRIPT"
    sed -i "s|{{MONGO_PASSWORD}}|$MONGO_PASSWORD|g" "$RESTORE_SCRIPT"
fi

# Make restore script executable
chmod +x "$RESTORE_SCRIPT"

# Download README from GitHub
README_FILE="$SCRIPTS_DIR/MONGODB_README.md"
curl -fsSL -o "$README_FILE" https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/MONGODB_README.md

# Replace placeholders in README
if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS
    sed -i '' "s|{{MONGO_PORT}}|$MONGO_PORT|g" "$README_FILE"
    sed -i '' "s|{{MONGO_DATABASE}}|$MONGO_DATABASE|g" "$README_FILE"
    sed -i '' "s|{{BACKUP_DIR}}|$BACKUP_DIR|g" "$README_FILE"
    sed -i '' "s|{{RETENTION_DAYS}}|$RETENTION_DAYS|g" "$README_FILE"
    sed -i '' "s|{{BACKUP_TIME}}|$BACKUP_TIME|g" "$README_FILE"
    sed -i '' "s|{{BACKUP_SCRIPT}}|$BACKUP_SCRIPT|g" "$README_FILE"
    sed -i '' "s|{{RESTORE_SCRIPT}}|$RESTORE_SCRIPT|g" "$README_FILE"
    sed -i '' "s|{{SCRIPTS_DIR}}|$SCRIPTS_DIR|g" "$README_FILE"
    sed -i '' "s|{{MONGO_USERNAME}}|$MONGO_USERNAME|g" "$README_FILE"
    sed -i '' "s|{{MONGO_PASSWORD}}|$MONGO_PASSWORD|g" "$README_FILE"
    sed -i '' "s|{{CREATED_DATE}}|$(date)|g" "$README_FILE"
else
    # Linux
    sed -i "s|{{MONGO_PORT}}|$MONGO_PORT|g" "$README_FILE"
    sed -i "s|{{MONGO_DATABASE}}|$MONGO_DATABASE|g" "$README_FILE"
    sed -i "s|{{BACKUP_DIR}}|$BACKUP_DIR|g" "$README_FILE"
    sed -i "s|{{RETENTION_DAYS}}|$RETENTION_DAYS|g" "$README_FILE"
    sed -i "s|{{BACKUP_TIME}}|$BACKUP_TIME|g" "$README_FILE"
    sed -i "s|{{BACKUP_SCRIPT}}|$BACKUP_SCRIPT|g" "$README_FILE"
    sed -i "s|{{RESTORE_SCRIPT}}|$RESTORE_SCRIPT|g" "$README_FILE"
    sed -i "s|{{SCRIPTS_DIR}}|$SCRIPTS_DIR|g" "$README_FILE"
    sed -i "s|{{MONGO_USERNAME}}|$MONGO_USERNAME|g" "$README_FILE"
    sed -i "s|{{MONGO_PASSWORD}}|$MONGO_PASSWORD|g" "$README_FILE"
    sed -i "s|{{CREATED_DATE}}|$(date)|g" "$README_FILE"
fi

echo ""
echo "=========================================="
echo "✅ MongoDB Setup Complete!"
echo "=========================================="
echo ""
CONN_STR_FINAL="MONGODB_CONNECTION=mongodb://$MONGO_USERNAME:$MONGO_PASSWORD@localhost:$MONGO_PORT/$MONGO_DATABASE?authSource=admin"
echo "Backups: $BACKUP_DIR (retention: $RETENTION_DAYS days, scheduled: $BACKUP_TIME daily)"
echo "Scripts: $SCRIPTS_DIR"
echo ""
echo "Commands:"
echo "  Logs:    docker logs -f telegram-coffee-mongodb"
echo "  Backup:  $BACKUP_SCRIPT"
echo "  Restore: $RESTORE_SCRIPT /path/to/backup.archive"
echo ""

# Return connection string for use by calling script
printf '%s\n' "Connection: mongodb://$MONGO_USERNAME:$MONGO_PASSWORD@localhost:$MONGO_PORT/$MONGO_DATABASE?authSource=admin"
# Output the raw MONGO connection in a reliable way and write to handshake file if requested
printf '%s\n' "$CONN_STR_FINAL"
if [ -n "${MONGO_HANDSHAKE_FILE:-}" ]; then
    echo "$CONN_STR_FINAL" > "$MONGO_HANDSHAKE_FILE" || true
    echo "MONGO_USERNAME=$MONGO_USERNAME" >> "$MONGO_HANDSHAKE_FILE" || true
    echo "MONGO_PASSWORD=$MONGO_PASSWORD" >> "$MONGO_HANDSHAKE_FILE" || true
    echo "MONGO_DATABASE=$MONGO_DATABASE" >> "$MONGO_HANDSHAKE_FILE" || true
    echo "MONGO_PORT=$MONGO_PORT" >> "$MONGO_HANDSHAKE_FILE" || true
fi
