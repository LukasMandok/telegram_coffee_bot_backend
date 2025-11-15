#!/bin/bash
# mongo_backup.sh — daily MongoDB dump with rotation

BACKUP_DIR="{{BACKUP_DIR}}"
DATE=$(date +%F)
CONTAINER_NAME="telegram-coffee-mongodb"
USER="{{MONGO_USERNAME}}"
PASS="{{MONGO_PASSWORD}}"
PORT={{MONGO_PORT}}
RETENTION_DAYS={{RETENTION_DAYS}}

mkdir -p "$BACKUP_DIR"

# Create backup inside container
docker exec "$CONTAINER_NAME" mongodump \
  --username "$USER" \
  --password "$PASS" \
  --authenticationDatabase admin \
  --port "$PORT" \
  --archive=/tmp/mongo_backup_$DATE.archive \
  --gzip

# Copy backup from container to host
docker cp "$CONTAINER_NAME:/tmp/mongo_backup_$DATE.archive" "$BACKUP_DIR/mongo_backup_$DATE.archive"

# Remove backup from container
docker exec "$CONTAINER_NAME" rm "/tmp/mongo_backup_$DATE.archive"

# Delete old backups
find "$BACKUP_DIR" -type f -mtime +$RETENTION_DAYS -name "mongo_backup_*.archive" -delete

echo "✅ Backup completed: $BACKUP_DIR/mongo_backup_$DATE.archive"
