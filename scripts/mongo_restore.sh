#!/bin/bash
# mongo_restore.sh — restore MongoDB from backup

if [ -z "$1" ]; then
    echo "Usage: $0 <backup_file>"
    echo ""
    echo "Available backups:"
    ls -lh {{BACKUP_DIR}}/mongo_backup_*.archive 2>/dev/null || echo "No backups found"
    exit 1
fi

BACKUP_FILE="$1"
CONTAINER_NAME="telegram-coffee-mongodb"
USER="{{MONGO_USERNAME}}"
PASS="{{MONGO_PASSWORD}}"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "❌ Error: Backup file not found: $BACKUP_FILE"
    exit 1
fi

echo "📥 Restoring MongoDB from: $BACKUP_FILE"
echo ""
read -p "This will overwrite existing data. Continue? [y/N]: " confirm

if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Restore cancelled."
    exit 0
fi

# Copy backup to container
docker cp "$BACKUP_FILE" "$CONTAINER_NAME:/tmp/restore.archive"

# Restore backup
docker exec "$CONTAINER_NAME" mongorestore \
  --username "$USER" \
  --password "$PASS" \
  --authenticationDatabase admin \
  --archive=/tmp/restore.archive \
  --gzip \
  --drop

# Remove backup from container
docker exec "$CONTAINER_NAME" rm "/tmp/restore.archive"

echo ""
echo "✅ Restore completed"
