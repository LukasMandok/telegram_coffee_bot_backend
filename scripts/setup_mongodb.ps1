# MongoDB Setup Script with Backup Configuration (PowerShell)
# This script sets up MongoDB in Docker with automated backups

$ErrorActionPreference = "Stop"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "MongoDB Setup" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# Default values
$DEFAULT_BACKUP_DIR = "$env:USERPROFILE\mongodb-backups"
$DEFAULT_SCRIPTS_DIR = "$env:USERPROFILE\scripts"
$DEFAULT_RETENTION_DAYS = 30
$DEFAULT_BACKUP_TIME = "03:00"
$DEFAULT_USERNAME = "admin"
$DEFAULT_PASSWORD = "password123"
$DEFAULT_DATABASE = "fastapi"
$DEFAULT_PORT = 27017

# Ask for MongoDB credentials
$inputUsername = Read-Host "MongoDB username [$DEFAULT_USERNAME]"
$MONGO_USERNAME = if ([string]::IsNullOrWhiteSpace($inputUsername)) { $DEFAULT_USERNAME } else { $inputUsername }

$inputPassword = Read-Host "MongoDB password [$DEFAULT_PASSWORD]"
$MONGO_PASSWORD = if ([string]::IsNullOrWhiteSpace($inputPassword)) { $DEFAULT_PASSWORD } else { $inputPassword }

$inputDatabase = Read-Host "MongoDB database [$DEFAULT_DATABASE]"
$MONGO_DATABASE = if ([string]::IsNullOrWhiteSpace($inputDatabase)) { $DEFAULT_DATABASE } else { $inputDatabase }

$inputPort = Read-Host "MongoDB port [$DEFAULT_PORT]"
$MONGO_PORT = if ([string]::IsNullOrWhiteSpace($inputPort)) { $DEFAULT_PORT } else { $inputPort }

Write-Host ""
# Ask for backup configuration
$inputBackupDir = Read-Host "Backup directory [$DEFAULT_BACKUP_DIR]"
$BACKUP_DIR = if ([string]::IsNullOrWhiteSpace($inputBackupDir)) { $DEFAULT_BACKUP_DIR } else { $inputBackupDir }

$inputScriptsDir = Read-Host "Scripts directory [$DEFAULT_SCRIPTS_DIR]"
$SCRIPTS_DIR = if ([string]::IsNullOrWhiteSpace($inputScriptsDir)) { $DEFAULT_SCRIPTS_DIR } else { $inputScriptsDir }

$inputRetention = Read-Host "Backup retention days [$DEFAULT_RETENTION_DAYS]"
$RETENTION_DAYS = if ([string]::IsNullOrWhiteSpace($inputRetention)) { $DEFAULT_RETENTION_DAYS } else { $inputRetention }

$inputTime = Read-Host "Backup time (HH:MM) [$DEFAULT_BACKUP_TIME]"
$BACKUP_TIME = if ([string]::IsNullOrWhiteSpace($inputTime)) { $DEFAULT_BACKUP_TIME } else { $inputTime }

Write-Host ""
Write-Host "🗄️  Setting up MongoDB container..." -ForegroundColor Yellow

# Stop and remove existing MongoDB container if it exists
try {
    docker stop telegram-coffee-mongodb 2>$null
    docker rm telegram-coffee-mongodb 2>$null
} catch {
    # Container doesn't exist, continue
}

# Create volume and start MongoDB container
try {
    docker volume create telegram-coffee-mongodb-data 2>$null
} catch {
    # Volume already exists, continue
}

docker run -d `
  --name telegram-coffee-mongodb `
  --restart unless-stopped `
  -p ${MONGO_PORT}:27017 `
  -e MONGO_INITDB_ROOT_USERNAME=$MONGO_USERNAME `
  -e MONGO_INITDB_ROOT_PASSWORD=$MONGO_PASSWORD `
  -e MONGO_INITDB_DATABASE=$MONGO_DATABASE `
  -v telegram-coffee-mongodb-data:/data/db `
  mongo:latest

Write-Host "✅ MongoDB container started" -ForegroundColor Green

# Wait for MongoDB to start
Start-Sleep -Seconds 5

Write-Host ""
Write-Host "📁 Setting up backup automation..." -ForegroundColor Yellow

# Create backup and scripts directories
New-Item -ItemType Directory -Force -Path $BACKUP_DIR | Out-Null
New-Item -ItemType Directory -Force -Path $SCRIPTS_DIR | Out-Null

# Download backup script from GitHub
$BACKUP_SCRIPT = Join-Path $SCRIPTS_DIR "mongo_backup.ps1"
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/mongo_backup.ps1" -OutFile $BACKUP_SCRIPT

# Replace placeholders in backup script
$backupContent = Get-Content $BACKUP_SCRIPT -Raw
$backupContent = $backupContent -replace "{{BACKUP_DIR}}", $BACKUP_DIR
$backupContent = $backupContent -replace "{{MONGO_USERNAME}}", $MONGO_USERNAME
$backupContent = $backupContent -replace "{{MONGO_PASSWORD}}", $MONGO_PASSWORD
$backupContent = $backupContent -replace "{{MONGO_PORT}}", $MONGO_PORT
$backupContent = $backupContent -replace "{{RETENTION_DAYS}}", $RETENTION_DAYS
Set-Content -Path $BACKUP_SCRIPT -Value $backupContent

# Test backup script
try {
    & powershell.exe -ExecutionPolicy Bypass -File $BACKUP_SCRIPT 2>$null | Out-Null
    Write-Host "✅ Backup script created and tested" -ForegroundColor Green
} catch {
    Write-Host "⚠️  Backup script created (initial test failed, but will work after MongoDB is ready)" -ForegroundColor Yellow
}

# Set up scheduled task
$taskName = "TelegramCoffeeBotMongoBackup"

# Remove existing task if it exists
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

# Parse backup time
$timeParts = $BACKUP_TIME -split ":"
$hour = [int]$timeParts[0]
$minute = [int]$timeParts[1]

# Create scheduled task
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -File `"$BACKUP_SCRIPT`" >> `"$SCRIPTS_DIR\mongo_backup.log`" 2>&1"
$trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours($hour).AddMinutes($minute))
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Daily MongoDB backup for Telegram Coffee Bot" | Out-Null

Write-Host "✅ Automated backup scheduled at $BACKUP_TIME daily" -ForegroundColor Green

# Download restore script from GitHub
$RESTORE_SCRIPT = Join-Path $SCRIPTS_DIR "mongo_restore.ps1"
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/mongo_restore.ps1" -OutFile $RESTORE_SCRIPT

# Replace placeholders in restore script
$restoreContent = Get-Content $RESTORE_SCRIPT -Raw
$restoreContent = $restoreContent -replace "{{BACKUP_DIR}}", $BACKUP_DIR
$restoreContent = $restoreContent -replace "{{MONGO_USERNAME}}", $MONGO_USERNAME
$restoreContent = $restoreContent -replace "{{MONGO_PASSWORD}}", $MONGO_PASSWORD
Set-Content -Path $RESTORE_SCRIPT -Value $restoreContent

# Download README from GitHub
$README_FILE = Join-Path $SCRIPTS_DIR "MONGODB_README.md"
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/MONGODB_README.md" -OutFile $README_FILE

# Replace placeholders in README
$readmeContent = Get-Content $README_FILE -Raw
$readmeContent = $readmeContent -replace "{{MONGO_PORT}}", $MONGO_PORT
$readmeContent = $readmeContent -replace "{{MONGO_DATABASE}}", $MONGO_DATABASE
$readmeContent = $readmeContent -replace "{{BACKUP_DIR}}", $BACKUP_DIR
$readmeContent = $readmeContent -replace "{{RETENTION_DAYS}}", $RETENTION_DAYS
$readmeContent = $readmeContent -replace "{{BACKUP_TIME}}", $BACKUP_TIME
$readmeContent = $readmeContent -replace "{{BACKUP_SCRIPT}}", $BACKUP_SCRIPT
$readmeContent = $readmeContent -replace "{{RESTORE_SCRIPT}}", $RESTORE_SCRIPT
$readmeContent = $readmeContent -replace "{{SCRIPTS_DIR}}", $SCRIPTS_DIR
$readmeContent = $readmeContent -replace "{{MONGO_USERNAME}}", $MONGO_USERNAME
$readmeContent = $readmeContent -replace "{{MONGO_PASSWORD}}", $MONGO_PASSWORD
$readmeContent = $readmeContent -replace "{{CREATED_DATE}}", (Get-Date).ToString()
Set-Content -Path $README_FILE -Value $readmeContent

# Skip old heredoc content
$null = @"

$readmeContent = @"
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

### mongo_backup.ps1
Performs a full MongoDB backup and saves it to the backup directory.

**Usage:**
``````powershell
$BACKUP_SCRIPT
``````

**Automated:** Runs daily at $BACKUP_TIME via Windows Task Scheduler

### mongo_restore.ps1
Restores MongoDB from a backup file.

**Usage:**
``````powershell
$RESTORE_SCRIPT -BackupFile "path\to\backup.archive"
``````

**List available backups:**
``````powershell
Get-ChildItem "$BACKUP_DIR\mongo_backup_*.archive"
``````

## Useful Commands

### View MongoDB logs
``````powershell
docker logs telegram-coffee-mongodb
docker logs -f telegram-coffee-mongodb  # follow logs
``````

### Connect to MongoDB shell
``````powershell
docker exec -it telegram-coffee-mongodb mongosh -u $MONGO_USERNAME -p $MONGO_PASSWORD --authenticationDatabase admin
``````

### Stop/Start MongoDB
``````powershell
docker stop telegram-coffee-mongodb
docker start telegram-coffee-mongodb
docker restart telegram-coffee-mongodb
``````

### View backup logs
``````powershell
Get-Content "$SCRIPTS_DIR\mongo_backup.log"
Get-Content "$SCRIPTS_DIR\mongo_backup.log" -Wait  # follow logs
``````

### Manual backup
``````powershell
$BACKUP_SCRIPT
``````

### View scheduled tasks
``````powershell
Get-ScheduledTask -TaskName "TelegramCoffeeBotMongoBackup"
``````

### Edit scheduled task
``````powershell
# Open Task Scheduler
taskschd.msc
``````

## MongoDB Connection String

``````
mongodb://$MONGO_USERNAME:$MONGO_PASSWORD@localhost:$MONGO_PORT/$MONGO_DATABASE
``````

## Notes

- Backups are stored in ``$BACKUP_DIR``
- Old backups are automatically deleted after $RETENTION_DAYS days
- The MongoDB data is stored in a Docker volume: ``telegram-coffee-mongodb-data``
- To completely remove MongoDB and all data: ``docker rm -f telegram-coffee-mongodb; docker volume rm telegram-coffee-mongodb-data``

---

**Created:** $(Get-Date)
**Author:** Telegram Coffee Bot Setup Script
"@

Set-Content -Path $README_FILE -Value $readmeContent

Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host "✅ MongoDB Setup Complete!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Connection: mongodb://$MONGO_USERNAME:$MONGO_PASSWORD@localhost:$MONGO_PORT/$MONGO_DATABASE" -ForegroundColor Gray
Write-Host "Backups: $BACKUP_DIR (retention: $RETENTION_DAYS days, scheduled: $BACKUP_TIME daily)" -ForegroundColor Gray
Write-Host "Scripts: $SCRIPTS_DIR" -ForegroundColor Gray
Write-Host ""
Write-Host "Commands:" -ForegroundColor Cyan
Write-Host "  Logs:    docker logs -f telegram-coffee-mongodb" -ForegroundColor Gray
Write-Host "  Backup:  $BACKUP_SCRIPT" -ForegroundColor Gray
Write-Host "  Restore: $RESTORE_SCRIPT -BackupFile 'path\to\backup.archive'" -ForegroundColor Gray
Write-Host ""

# Return connection string for use by calling script
Write-Output "MONGODB_CONNECTION=mongodb://$MONGO_USERNAME`:$MONGO_PASSWORD@localhost:$MONGO_PORT/$MONGO_DATABASE"
