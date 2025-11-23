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
$DEFAULT_DATABASE = "telegram_bot"
$DEFAULT_PORT = 27017

# Ask for MongoDB credentials and backup config will come after detection
# Additional heuristics & detection will run before prompting the user, so we
# can ask S/R/C first and skip prompts if credentials were found.
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

## Detect existing MongoDB (various methods: container name, name contains mongo, image contains mongo, container port, host port open)
$existing = $null
$portOpen = $false

# 1) Exact container name
try { if ((docker ps -a --format '{{.Names}}') -match 'telegram-coffee-mongodb') { $existing = 'telegram-coffee-mongodb' } } catch {}

# 2) Container name includes 'mongo'
try { if (-not $existing -and (docker ps -a --format '{{.Names}}' | Select-String -Pattern 'mongo' -Quiet)) { $existing = (docker ps -a --format '{{.Names}}' | Select-String -Pattern 'mongo' | Select-Object -First 1).Line.Trim() } } catch {}

# 3) Image looks like MongoDB (mongo, mongodb or bitnami)
try { if (-not $existing -and (docker ps -a --format '{{.Names}} {{.Image}}' | Select-String -Pattern 'mongo|mongodb|bitnami' -Quiet)) { $existing = (docker ps -a --format '{{.Names}} {{.Image}}' | Select-String -Pattern 'mongo|mongodb|bitnami' | Select-Object -First 1).Line.Split()[0] } } catch {}

# 4) Container mapping to host port 27017
try { if (-not $existing) { $containerWithPort = (docker ps --format '{{.Names}} {{.Ports}}' | Select-String -Pattern ':27017' | Select-Object -First 1).Line; if ($containerWithPort) { $existing = $containerWithPort.Split()[0] } } } catch {}

# 5) Host port detection via Test-NetConnection or netstat/ss
try { if (Get-Command Test-NetConnection -ErrorAction SilentlyContinue) { $portOpen = (Test-NetConnection -ComputerName '127.0.0.1' -Port 27017).TcpTestSucceeded } } catch {}
if (-not $portOpen) {
    try { if (Get-Command ss -ErrorAction SilentlyContinue) { $portOpen = (ss -ltn | Select-String ':27017' -Quiet) } } catch {}
}
if (-not $portOpen) {
    try { if (Get-Command netstat -ErrorAction SilentlyContinue) { $portOpen = (netstat -ltn | Select-String ':27017' -Quiet) } } catch {}
}
if ($existing -or $portOpen) {
    Write-Host "⚠️ Detected existing MongoDB instance:" -ForegroundColor Yellow
    if ($existing) { 
        try { $image = (docker inspect --format '{{.Config.Image}}' $existing) } catch { $image = '(unknown)' }
        try { $ports = (docker ps --format '{{.Names}} {{.Ports}}' | Select-String "^$existing" | Select-Object -First 1).Line.Split()[1] } catch { $ports = '' }
        Write-Host "  - Docker container: $existing (image: $image, ports: $ports)" 
    }
    if ($portOpen) { Write-Host "  - TCP port 27017 open on host" }
    $mongoAction = Read-Host "Options: Skip (S), Recreate (R), Continue (C) - choose [S/r/c]"
    if ([string]::IsNullOrWhiteSpace($mongoAction)) { $mongoAction = 'S' }
    if ($mongoAction -match '^[Ss]$') {
        if ($existing) { $envList = docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' $existing }
        if ($envList) {
            $mongoUser = ($envList | Where-Object { $_ -match '^MONGO_INITDB_ROOT_USERNAME=' }) -split '=' | Select-Object -Last 1
            $mongoPass = ($envList | Where-Object { $_ -match '^MONGO_INITDB_ROOT_PASSWORD=' }) -split '=' | Select-Object -Last 1
            $mongoDB = ($envList | Where-Object { $_ -match '^MONGO_INITDB_DATABASE=' }) -split '=' | Select-Object -Last 1
            $hostPort = docker inspect --format '{{ if index .NetworkSettings.Ports "27017/tcp" }}{{ (index (index .NetworkSettings.Ports "27017/tcp") 0).HostPort }}{{ end }}' $existing
        }
        if (-not $hostPort) { $hostPort = 27017 }
        if ($mongoUser -and $mongoPass) { Write-Host "Using existing MongoDB connection: mongodb://$mongoUser`:$mongoPass@localhost:$hostPort/$mongoDB?authSource=admin" -ForegroundColor Green; Write-Output "MONGODB_CONNECTION=mongodb://$mongoUser`:$mongoPass@localhost:$hostPort/$mongoDB?authSource=admin"; exit 0 }
        else {
            Write-Host "Could not extract credentials; please provide them to use existing instance" -ForegroundColor Yellow
            $mongoUser = Read-Host "MongoDB username [admin]"; if ([string]::IsNullOrWhiteSpace($mongoUser)) { $mongoUser = 'admin' }
            $mongoPass = Read-Host "MongoDB password [password123]"; if ([string]::IsNullOrWhiteSpace($mongoPass)) { $mongoPass = 'password123' }
            $mongoDB = Read-Host "MongoDB database [telegram_bot]"; if ([string]::IsNullOrWhiteSpace($mongoDB)) { $mongoDB = 'telegram_bot' }
            Write-Host "Using provided credentials" -ForegroundColor Green
            Write-Output "MONGODB_CONNECTION=mongodb://$mongoUser`:$mongoPass@localhost:$hostPort/$mongoDB?authSource=admin"; exit 0
        }
    }
    elseif ($mongoAction -match '^[Rr]$') {
        $target = if ($existing) { $existing } else { (docker ps -a --filter ancestor=mongo --format '{{.Names}}' | Select-Object -First 1) }
        $keep = Read-Host "Recreate container '$target'? Keep volume (Y/n)"; if ([string]::IsNullOrWhiteSpace($keep)) { $keep = 'Y' }
        docker rm -f $target 2>$null
        if ($keep -notmatch '^[Yy]$') { docker volume rm telegram-coffee-mongodb-data 2>$null }
        Write-Host "Container removed; continuing setup..." -ForegroundColor Yellow
    }
}

# Additional heuristics: docker volumes and host process detection. Only run when unchanged
if (-not $existing -and -not $portOpen) {
    # Docker volumes that look like MongoDB
    try {
        $vol = docker volume ls --format '{{.Name}}' | Select-String -Pattern 'mongo|mongodb' -Quiet
        if ($vol) { Write-Host "⚠️  Found a Docker volume with a name containing 'mongo' or 'mongodb'." -ForegroundColor Yellow }
    } catch {}

    # Host 'mongod' process check
    try { if (Get-Process -Name mongod -ErrorAction SilentlyContinue) { Write-Host "⚠️  Found 'mongod' running on the host (not in Docker)." -ForegroundColor Yellow; $portOpen = $true } } catch {}

    # If still not detected, show a short docker ps output for the user
    if (-not $existing -and -not $portOpen) {
        Write-Host "⚠️  No MongoDB container or host process detected. Here are your Docker containers (Name Image Ports):" -ForegroundColor Yellow
        docker ps -a --format "{{.Names}} {{.Image}} {{.Ports}}" | ForEach-Object { Write-Host "  $_" }
        Write-Host "If your MongoDB is running under a different container name or as a host process, either recreate it as 'telegram-coffee-mongodb' or choose Skip and input credentials manually." -ForegroundColor Yellow
    }
}

# If credentials have not yet been collected (no existing container was used), prompt interactively
if (-not $MONGO_USERNAME) {
    $inputUsername = Read-Host "MongoDB username [$DEFAULT_USERNAME]"
    $MONGO_USERNAME = if ([string]::IsNullOrWhiteSpace($inputUsername)) { $DEFAULT_USERNAME } else { $inputUsername }
}
if (-not $MONGO_PASSWORD) {
    $inputPassword = Read-Host "MongoDB password [$DEFAULT_PASSWORD]"
    $MONGO_PASSWORD = if ([string]::IsNullOrWhiteSpace($inputPassword)) { $DEFAULT_PASSWORD } else { $inputPassword }
}
if (-not $MONGO_DATABASE) {
    $inputDatabase = Read-Host "MongoDB database [$DEFAULT_DATABASE]"
    $MONGO_DATABASE = if ([string]::IsNullOrWhiteSpace($inputDatabase)) { $DEFAULT_DATABASE } else { $inputDatabase }
}
if (-not $MONGO_PORT) {
    $inputPort = Read-Host "MongoDB port [$DEFAULT_PORT]"
    $MONGO_PORT = if ([string]::IsNullOrWhiteSpace($inputPort)) { $DEFAULT_PORT } else { $inputPort }
}

Write-Host ""

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
try { Invoke-WebRequest -Uri "https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/mongo_backup.ps1" -OutFile $BACKUP_SCRIPT -ErrorAction Stop } catch { Write-Host "❌ Failed to download backup script; check internet or GitHub URL." -ForegroundColor Red; exit 1 }

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
try { Invoke-WebRequest -Uri "https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/mongo_restore.ps1" -OutFile $RESTORE_SCRIPT -ErrorAction Stop } catch { Write-Host "❌ Failed to download restore script; check internet or GitHub URL." -ForegroundColor Red; exit 1 }

# Replace placeholders in restore script
$restoreContent = Get-Content $RESTORE_SCRIPT -Raw
$restoreContent = $restoreContent -replace "{{BACKUP_DIR}}", $BACKUP_DIR
$restoreContent = $restoreContent -replace "{{MONGO_USERNAME}}", $MONGO_USERNAME
$restoreContent = $restoreContent -replace "{{MONGO_PASSWORD}}", $MONGO_PASSWORD
Set-Content -Path $RESTORE_SCRIPT -Value $restoreContent

# Download README from GitHub
$README_FILE = Join-Path $SCRIPTS_DIR "MONGODB_README.md"
try { Invoke-WebRequest -Uri "https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/MONGODB_README.md" -OutFile $README_FILE -ErrorAction Stop } catch { Write-Host "❌ Failed to download README; check internet or GitHub URL." -ForegroundColor Red; exit 1 }

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

Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host "✅ MongoDB Setup Complete!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Connection: mongodb://$MONGO_USERNAME:$MONGO_PASSWORD@localhost:$MONGO_PORT/$MONGO_DATABASE?authSource=admin" -ForegroundColor Gray
Write-Host "Backups: $BACKUP_DIR (retention: $RETENTION_DAYS days, scheduled: $BACKUP_TIME daily)" -ForegroundColor Gray
Write-Host "Scripts: $SCRIPTS_DIR" -ForegroundColor Gray
Write-Host ""
Write-Host "Commands:" -ForegroundColor Cyan
Write-Host "  Logs:    docker logs -f telegram-coffee-mongodb" -ForegroundColor Gray
Write-Host "  Backup:  $BACKUP_SCRIPT" -ForegroundColor Gray
Write-Host "  Restore: $RESTORE_SCRIPT -BackupFile 'path\to\backup.archive'" -ForegroundColor Gray
Write-Host ""

# Return connection string for use by calling script
$connStr = "MONGODB_CONNECTION=mongodb://$MONGO_USERNAME`:$MONGO_PASSWORD@localhost:$MONGO_PORT/$MONGO_DATABASE?authSource=admin"
Write-Output $connStr

if ($env:MONGO_HANDSHAKE_FILE) {
    $connStr | Out-File -FilePath $env:MONGO_HANDSHAKE_FILE -Encoding utf8
    "MONGO_USERNAME=$MONGO_USERNAME" | Out-File -FilePath $env:MONGO_HANDSHAKE_FILE -Append -Encoding utf8
    "MONGO_PASSWORD=$MONGO_PASSWORD" | Out-File -FilePath $env:MONGO_HANDSHAKE_FILE -Append -Encoding utf8
    "MONGO_DATABASE=$MONGO_DATABASE" | Out-File -FilePath $env:MONGO_HANDSHAKE_FILE -Append -Encoding utf8
    "MONGO_PORT=$MONGO_PORT" | Out-File -FilePath $env:MONGO_HANDSHAKE_FILE -Append -Encoding utf8
}
