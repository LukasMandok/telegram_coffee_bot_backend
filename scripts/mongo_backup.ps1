# mongo_backup.ps1 — daily MongoDB dump with rotation

$BACKUP_DIR = "{{BACKUP_DIR}}"
$DATE = Get-Date -Format "yyyy-MM-dd"
$CONTAINER_NAME = "telegram-coffee-mongodb"
$USER = "{{MONGO_USERNAME}}"
$PASS = "{{MONGO_PASSWORD}}"
$PORT = {{MONGO_PORT}}
$RETENTION_DAYS = {{RETENTION_DAYS}}

# Ensure backup directory exists
New-Item -ItemType Directory -Force -Path $BACKUP_DIR | Out-Null

# Create backup inside container
docker exec $CONTAINER_NAME mongodump `
  --username $USER `
  --password $PASS `
  --authenticationDatabase admin `
  --port $PORT `
  --archive=/tmp/mongo_backup_$DATE.archive `
  --gzip

# Copy backup from container to host
docker cp "$CONTAINER_NAME:/tmp/mongo_backup_$DATE.archive" "$BACKUP_DIR\mongo_backup_$DATE.archive"

# Remove backup from container
docker exec $CONTAINER_NAME rm "/tmp/mongo_backup_$DATE.archive"

# Delete old backups
Get-ChildItem -Path $BACKUP_DIR -Filter "mongo_backup_*.archive" | 
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$RETENTION_DAYS) } | 
    Remove-Item -Force

Write-Host "✅ Backup completed: $BACKUP_DIR\mongo_backup_$DATE.archive" -ForegroundColor Green
