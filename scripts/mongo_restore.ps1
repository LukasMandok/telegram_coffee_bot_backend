# mongo_restore.ps1 — restore MongoDB from backup

param(
    [Parameter(Mandatory=$false)]
    [string]$BackupFile
)

$BACKUP_DIR = "{{BACKUP_DIR}}"
$CONTAINER_NAME = "telegram-coffee-mongodb"
$USER = "{{MONGO_USERNAME}}"
$PASS = "{{MONGO_PASSWORD}}"

if ([string]::IsNullOrWhiteSpace($BackupFile)) {
    Write-Host "Usage: .\mongo_restore.ps1 <backup_file>" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Available backups:" -ForegroundColor Cyan
    Get-ChildItem -Path $BACKUP_DIR -Filter "mongo_backup_*.archive" | Format-Table Name, Length, LastWriteTime -AutoSize
    exit 1
}

if (-not (Test-Path $BackupFile)) {
    Write-Host "❌ Error: Backup file not found: $BackupFile" -ForegroundColor Red
    exit 1
}

Write-Host "📥 Restoring MongoDB from: $BackupFile" -ForegroundColor Yellow
Write-Host ""
$confirm = Read-Host "This will overwrite existing data. Continue? [y/N]"

if ($confirm -notmatch "^[Yy]$") {
    Write-Host "Restore cancelled." -ForegroundColor Yellow
    exit 0
}

# Copy backup to container
docker cp "$BackupFile" "$CONTAINER_NAME:/tmp/restore.archive"

# Restore backup
docker exec $CONTAINER_NAME mongorestore `
  --username $USER `
  --password $PASS `
  --authenticationDatabase admin `
  --archive=/tmp/restore.archive `
  --gzip `
  --drop

# Remove backup from container
docker exec $CONTAINER_NAME rm "/tmp/restore.archive"

Write-Host ""
Write-Host "✅ Restore completed" -ForegroundColor Green
