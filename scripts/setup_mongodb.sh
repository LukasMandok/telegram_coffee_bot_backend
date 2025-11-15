#!/bin/bash
# MongoDB Setup Script with Backup Configuration
# This script sets up MongoDB in Docker with automated backups

set -e

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
DEFAULT_DATABASE="fastapi"
DEFAULT_PORT=27017

# Ask for MongoDB credentials
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
curl -L -o "$BACKUP_SCRIPT" https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/mongo_backup.sh

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

# Remove existing cron job if it exists
crontab -l 2>/dev/null | grep -v "mongo_backup.sh" | grep -v "Telegram Coffee Bot MongoDB Backup" | crontab - 2>/dev/null || true

# Add new cron job
(crontab -l 2>/dev/null; echo "$CRON_COMMENT"; echo "$CRON_JOB") | crontab -

echo "✅ Automated backup scheduled at $BACKUP_TIME daily"

# Download restore script from GitHub
RESTORE_SCRIPT="$SCRIPTS_DIR/mongo_restore.sh"
curl -L -o "$RESTORE_SCRIPT" https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/mongo_restore.sh

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
curl -L -o "$README_FILE" https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/MONGODB_README.md

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

# Remove old heredoc content (will be skipped)
cat > /dev/null << EOF
# MongoDB Management Scripts

## Overview

This directory contains scripts for managing the Telegram Coffee Bot MongoDB instance.

## Configuration

- **Container Name:** telegram-coffee-mongodb
- **Port:** $MONGO_PORT
- **Database:** $MONGO_DATABASE
- **Backup Directory:** $BACKUP_DIR
- **Retention Period:** $RETENTION_DAYS days
- **Backup Time:** Daily at $BACKUP_TIME

## Scripts

### mongo_backup.sh
Performs a full MongoDB backup and saves it to the backup directory.

**Usage:**
\`\`\`bash
$BACKUP_SCRIPT
\`\`\`

**Automated:** Runs daily at $BACKUP_TIME via cron

### mongo_restore.sh
Restores MongoDB from a backup file.

**Usage:**
\`\`\`bash
$RESTORE_SCRIPT /path/to/backup.archive
\`\`\`

**List available backups:**
\`\`\`bash
ls -lh $BACKUP_DIR/
\`\`\`

## Useful Commands

### View MongoDB logs
\`\`\`bash
docker logs telegram-coffee-mongodb
docker logs -f telegram-coffee-mongodb  # follow logs
\`\`\`

### Connect to MongoDB shell
\`\`\`bash
docker exec -it telegram-coffee-mongodb mongosh -u $MONGO_USERNAME -p $MONGO_PASSWORD --authenticationDatabase admin
\`\`\`

### Stop/Start MongoDB
\`\`\`bash
docker stop telegram-coffee-mongodb
docker start telegram-coffee-mongodb
docker restart telegram-coffee-mongodb
\`\`\`

### View backup logs
\`\`\`bash
cat $SCRIPTS_DIR/mongo_backup.log
tail -f $SCRIPTS_DIR/mongo_backup.log  # follow logs
\`\`\`

### Manual backup
\`\`\`bash
$BACKUP_SCRIPT
\`\`\`

### List cron jobs
\`\`\`bash
crontab -l
\`\`\`

### Edit cron jobs
\`\`\`bash
crontab -e
\`\`\`

## MongoDB Connection String

\`\`\`
mongodb://$MONGO_USERNAME:$MONGO_PASSWORD@localhost:$MONGO_PORT/$MONGO_DATABASE
\`\`\`

## Notes

- Backups are stored in \`$BACKUP_DIR\`
- Old backups are automatically deleted after $RETENTION_DAYS days
- The MongoDB data is stored in a Docker volume: \`telegram-coffee-mongodb-data\`
- To completely remove MongoDB and all data: \`docker rm -f telegram-coffee-mongodb && docker volume rm telegram-coffee-mongodb-data\`

---

**Created:** $(date)
**Author:** Telegram Coffee Bot Setup Script
EOF

echo ""
echo "=========================================="
echo "✅ MongoDB Setup Complete!"
echo "=========================================="
echo ""
echo "Connection: mongodb://$MONGO_USERNAME:$MONGO_PASSWORD@localhost:$MONGO_PORT/$MONGO_DATABASE"
echo "Backups: $BACKUP_DIR (retention: $RETENTION_DAYS days, scheduled: $BACKUP_TIME daily)"
echo "Scripts: $SCRIPTS_DIR"
echo ""
echo "Commands:"
echo "  Logs:    docker logs -f telegram-coffee-mongodb"
echo "  Backup:  $BACKUP_SCRIPT"
echo "  Restore: $RESTORE_SCRIPT /path/to/backup.archive"
echo ""

# Return connection string for use by calling script
echo "MONGODB_CONNECTION=mongodb://$MONGO_USERNAME:$MONGO_PASSWORD@localhost:$MONGO_PORT/$MONGO_DATABASE"
