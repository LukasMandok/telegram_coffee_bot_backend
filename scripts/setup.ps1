# Telegram Coffee Bot - Quick Setup Script (PowerShell)
# This script downloads the latest Docker image and sets up the environment

$ErrorActionPreference = "Stop"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Telegram Coffee Bot - Quick Setup" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# Create and enter installation directory (unless in dev/git environment)
$installDir = "telegram_bot"
if ((Test-Path ".git") -or (Test-Path "docker-compose.yml") -or (Test-Path "../docker-compose.yml")) {
    Write-Host "ℹ️  Dev/Repo detected: Installing in current directory." -ForegroundColor Gray
} else {
    if (-not (Test-Path $installDir)) {
        Write-Host "📁 Creating installation directory: $installDir" -ForegroundColor Cyan
        New-Item -ItemType Directory -Force -Path $installDir | Out-Null
    }
    Write-Host "📂 Entering directory: $installDir" -ForegroundColor Cyan
    Set-Location $installDir
}

# Check required/optional dependencies
$reqProgs = @('docker')
$missing = @()
foreach ($p in $reqProgs) { if (-not (Get-Command $p -ErrorAction SilentlyContinue)) { $missing += $p } }
if ($missing.Count -gt 0) { Write-Host "❌ Missing programs: $($missing -join ', ')" -ForegroundColor Red; Write-Host "Please install them and run the setup again." -ForegroundColor Yellow; exit 1 }
$reqCmdlets = @('Invoke-WebRequest')
$missingCmdlets = @()
foreach ($c in $reqCmdlets) { if (-not (Get-Command $c -ErrorAction SilentlyContinue)) { $missingCmdlets += $c } }
if ($missingCmdlets.Count -gt 0) { Write-Host "❌ Missing PowerShell cmdlets: $($missingCmdlets -join ', ')" -ForegroundColor Red; Write-Host "Please run a modern PowerShell (Invoke-WebRequest is required) and re-run the setup." -ForegroundColor Yellow; exit 1 }
$optionalProgs = @('code')
$missOpt = @(); foreach ($o in $optionalProgs) { if (-not (Get-Command $o -ErrorAction SilentlyContinue)) { $missOpt += $o } }
if ($missOpt.Count -gt 0) { Write-Host "⚠️ Optional: $($missOpt -join ', ') not found (editors). The setup will continue." -ForegroundColor Yellow }

# Check if Docker is installed
try {
    $null = docker --version
    Write-Host "✅ Docker is installed" -ForegroundColor Green
} catch {
    Write-Host "❌ Error: Docker is not installed." -ForegroundColor Red
    Write-Host "Please install Docker from: https://docs.docker.com/get-docker/"
    exit 1
}

# Check if Docker is running
try {
    $null = docker info 2>$null
    Write-Host "✅ Docker is running" -ForegroundColor Green
} catch {
    Write-Host "❌ Error: Docker is not running." -ForegroundColor Red
    Write-Host "Please start Docker Desktop and try again."
    exit 1
}

Write-Host ""

# Ask about MongoDB setup
Write-Host "Do you want to set up MongoDB in a Docker container with automated backups? (y/n)"
Write-Host "Note: If you already have MongoDB running, select 'n'"
$setupMongo = $null
try {
    $isRedirected = [Console]::IsInputRedirected
} catch {
    $isRedirected = $false
}
if ($isRedirected) {
    # Non-interactive environment - default to skip unless env var is set
    Write-Host "Non-interactive: MongoDB setup will be skipped by default (set SETUP_MONGO=y to override)." -ForegroundColor Yellow
    if ($env:SETUP_MONGO) { $setupMongo = $env:SETUP_MONGO } elseif ($env:setupMongo) { $setupMongo = $env:setupMongo }
    else { $setupMongo = 'n' }
} else {
    $setupMongo = Read-Host "Setup MongoDB? [y/N]"
    if ([string]::IsNullOrWhiteSpace($setupMongo)) { $setupMongo = 'n' }
}

if ($setupMongo -match "^[Yy]$") {
    Write-Host ""
    # MongoDB child script handles existing-instance detection and prompts
    Write-Host "📥 Downloading MongoDB setup script..." -ForegroundColor Yellow
    
    # Check if local setup_mongodb.ps1 exists (dev mode)
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
    $localScript = Join-Path $scriptDir "setup_mongodb.ps1"
    
    if (Test-Path $localScript) {
        Write-Host "   Using local setup_mongodb.ps1" -ForegroundColor Cyan
        Copy-Item $localScript "setup_mongodb_temp.ps1" -Force
    } elseif (Test-Path ".\setup_mongodb.ps1") {
        Write-Host "   Using local .\setup_mongodb.ps1" -ForegroundColor Cyan
        Copy-Item ".\setup_mongodb.ps1" "setup_mongodb_temp.ps1" -Force
    } else {
        # Download setup_mongodb.ps1 from GitHub
        try {
            Invoke-WebRequest -Uri "https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/scripts/setup_mongodb.ps1" -OutFile "setup_mongodb_temp.ps1" -ErrorAction Stop
        } catch {
            Write-Host "❌ Failed to download MongoDB setup script. Check your internet / URL and try again." -ForegroundColor Red
            exit 1
        }
    }
    
    # Run MongoDB setup script interactively so prompts are visible to the user
    $handshake = [System.IO.Path]::GetTempFileName()
    $env:MONGO_HANDSHAKE_FILE = $handshake
    Write-Host "↪ Running MongoDB setup script now. This script is interactive and will prompt for credentials and options." -ForegroundColor Yellow
    & powershell.exe -ExecutionPolicy Bypass -File ".\setup_mongodb_temp.ps1"

    # Extract connection string and variables from the handshake file if it exists
    $connectionLine = $null
    $mongoUser = $null; $mongoPass = $null; $mongoDB = $null; $mongoPort = $null
    
    if (Test-Path $handshake) { 
        $content = Get-Content $handshake
        $connectionLine = $content | Where-Object { $_ -match "MONGODB_CONNECTION=" }
        
        $mongoUser = ($content | Where-Object { $_ -match "MONGO_USERNAME=" } | Select-Object -First 1) -replace "MONGO_USERNAME=", ""
        $mongoPass = ($content | Where-Object { $_ -match "MONGO_PASSWORD=" } | Select-Object -First 1) -replace "MONGO_PASSWORD=", ""
        $mongoDB = ($content | Where-Object { $_ -match "MONGO_DATABASE=" } | Select-Object -First 1) -replace "MONGO_DATABASE=", ""
        $mongoPort = ($content | Where-Object { $_ -match "MONGO_PORT=" } | Select-Object -First 1) -replace "MONGO_PORT=", ""
    }
    
    if ($connectionLine) {
        $dbUrl = ($connectionLine -split "=", 2)[1]
        Write-Host "Detected MongoDB connection: $dbUrl" -ForegroundColor Cyan
    } else {
        # Fallback to default if connection string not found
        $dbUrl = "mongodb://admin:password123@localhost:27017/telegram_bot"
    }
    
    # Clean up temporary script and handshake
    Remove-Item "setup_mongodb_temp.ps1" -Force -ErrorAction SilentlyContinue
    if (Test-Path $handshake) { Remove-Item $handshake -Force -ErrorAction SilentlyContinue }
} else {
    Write-Host "⏭️  Skipping MongoDB setup" -ForegroundColor Yellow
    Write-Host ""
    $dbUrl = "mongodb://admin:password123@localhost:27017/telegram_bot"
}

# Download .env.example if .env doesn't exist
if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Write-Host "   Using local .env.example" -ForegroundColor Cyan
    } elseif (Test-Path "../.env.example") {
        Write-Host "   Using local ../.env.example" -ForegroundColor Cyan
        Copy-Item "../.env.example" ".env.example"
    } else {
        Write-Host "📥 Downloading .env.example template..." -ForegroundColor Yellow
        try {
            Invoke-WebRequest -Uri "https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/.env.example" -OutFile ".env.example" -ErrorAction Stop
        } catch {
            Write-Host "❌ Failed to download .env.example. Check your internet / URL and try again." -ForegroundColor Red
            exit 1
        }
    }
    
    Write-Host "📝 Creating .env file from template..." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
    
    # Set the MongoDB variables in .env
    $envContent = Get-Content ".env"
    if ($mongoUser) { $envContent = $envContent -replace "MONGO_INITDB_ROOT_USERNAME=.*", "MONGO_INITDB_ROOT_USERNAME=$mongoUser" }
    if ($mongoPass) { $envContent = $envContent -replace "MONGO_INITDB_ROOT_PASSWORD=.*", "MONGO_INITDB_ROOT_PASSWORD=$mongoPass" }
    if ($mongoDB) { $envContent = $envContent -replace "MONGO_INITDB_DATABASE=.*", "MONGO_INITDB_DATABASE=$mongoDB" }
    
    if ($mongoPort) {
        if ($envContent -match "MONGO_PORT=") {
            $envContent = $envContent -replace "MONGO_PORT=.*", "MONGO_PORT=$mongoPort"
        } else {
            $envContent += "MONGO_PORT=$mongoPort"
        }
    }

    # Comment out DATABASE_URL
    $envContent = $envContent -replace "^DATABASE_URL=", "# DATABASE_URL="
    $envContent | Set-Content ".env"
    
    Write-Host ""
    Write-Host "⚠️  IMPORTANT: You need to edit the .env file with your credentials!" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Required variables:" -ForegroundColor White
    Write-Host "  - API_ID: Your Telegram API ID (get from https://my.telegram.org)" -ForegroundColor Gray
    Write-Host "  - API_HASH: Your Telegram API Hash" -ForegroundColor Gray
    Write-Host "  - BOT_TOKEN: Your Telegram Bot Token (get from @BotFather)" -ForegroundColor Gray
    Write-Host "  - BOT_HOST: Your bot host URL" -ForegroundColor Gray
    Write-Host "  - DEFAULT_ADMIN: Your Telegram user ID" -ForegroundColor Gray
    Write-Host ""
    Write-Host "The MongoDB configuration has been updated." -ForegroundColor Gray
    Write-Host ""
    
    # Open .env in default editor; prefer VS Code (code) if installed
    Write-Host "Opening .env file in default editor..." -ForegroundColor Yellow
    $editorOpened = $false
    if (Get-Command code -ErrorAction SilentlyContinue) {
        try { Start-Process code -ArgumentList '--wait', '.env'; $editorOpened = $true } catch {}
    }
    
    if (-not $editorOpened) {
        if ($IsLinux) {
            if (Get-Command nano -ErrorAction SilentlyContinue) {
                if ([Console]::IsInputRedirected) { bash -c "nano .env < /dev/tty" } else { nano .env }
                $editorOpened = $true
            } elseif (Get-Command vim -ErrorAction SilentlyContinue) {
                if ([Console]::IsInputRedirected) { bash -c "vim .env < /dev/tty" } else { vim .env }
                $editorOpened = $true
            }
        } else {
            try { Start-Process notepad.exe ".env"; $editorOpened = $true } catch {}
        }
    }
    
    if (-not $editorOpened) {
        Write-Host "Could not open an editor automatically. Please edit .env manually: $(Join-Path (Get-Location) '.env')" -ForegroundColor Yellow
    }
    
    Read-Host "Press Enter after you've edited and saved the .env file"
} else {
    Write-Host "✅ .env file already exists" -ForegroundColor Green
    # If a MongoDB setup was performed, offer to update the variables in the existing .env
    if ($setupMongo -match "^[Yy]$") {
        $updateDbUrl = Read-Host "Do you want to update MongoDB variables in the existing .env? [Y/n]"
        if ([string]::IsNullOrWhiteSpace($updateDbUrl)) { $updateDbUrl = "Y" }
        if ($updateDbUrl -match "^[Yy]$") {
            try {
                $envContent = Get-Content ".env"
                if ($mongoUser) { $envContent = $envContent -replace "MONGO_INITDB_ROOT_USERNAME=.*", "MONGO_INITDB_ROOT_USERNAME=$mongoUser" }
                if ($mongoPass) { $envContent = $envContent -replace "MONGO_INITDB_ROOT_PASSWORD=.*", "MONGO_INITDB_ROOT_PASSWORD=$mongoPass" }
                if ($mongoDB) { $envContent = $envContent -replace "MONGO_INITDB_DATABASE=.*", "MONGO_INITDB_DATABASE=$mongoDB" }
                
                if ($mongoPort) {
                    if ($envContent -match "MONGO_PORT=") {
                        $envContent = $envContent -replace "MONGO_PORT=.*", "MONGO_PORT=$mongoPort"
                    } else {
                        $envContent += "MONGO_PORT=$mongoPort"
                    }
                }

                $envContent = $envContent -replace "^DATABASE_URL=", "# DATABASE_URL="
                $envContent | Set-Content ".env"
                Write-Host "✅ MongoDB variables updated in .env" -ForegroundColor Green
            } catch {
                Write-Host "⚠️ Could not update .env automatically, please edit it manually." -ForegroundColor Yellow
            }
        }
    }
    $editEnv = Read-Host "Do you want to edit .env now? [y/N]"
    if ([string]::IsNullOrWhiteSpace($editEnv)) { $editEnv = "n" }
    if ($editEnv -match "^[Yy]$") {
        Write-Host "Opening .env in default editor..." -ForegroundColor Yellow
        $editorOpened = $false
        if (Get-Command code -ErrorAction SilentlyContinue) {
            try { Start-Process code -ArgumentList '--wait', '.env'; $editorOpened = $true } catch {}
        }
        
        if (-not $editorOpened) {
            if ($IsLinux) {
                if (Get-Command nano -ErrorAction SilentlyContinue) {
                    if ([Console]::IsInputRedirected) { bash -c "nano .env < /dev/tty" } else { nano .env }
                    $editorOpened = $true
                } elseif (Get-Command vim -ErrorAction SilentlyContinue) {
                    if ([Console]::IsInputRedirected) { bash -c "vim .env < /dev/tty" } else { vim .env }
                    $editorOpened = $true
                }
            } else {
                try { Start-Process notepad.exe ".env"; $editorOpened = $true } catch {}
            }
        }
        
        if (-not $editorOpened) {
            Write-Host "Could not open an editor automatically. Please edit .env manually: $(Join-Path (Get-Location) '.env')" -ForegroundColor Yellow
        }
        Read-Host "Press Enter after you've edited and saved the .env file"
    }
} 

Write-Host ""
Write-Host "🐳 Pulling latest Docker image from GitHub Container Registry..." -ForegroundColor Yellow
# docker pull ghcr.io/lukasmandok/telegram_coffee_bot_backend:main

# Download docker-compose.deploy.yml
if (Test-Path "docker-compose.deploy.yml") {
    Write-Host "   Using local docker-compose.deploy.yml" -ForegroundColor Cyan
} elseif (Test-Path "../docker-compose.deploy.yml") {
    Write-Host "   Using local ../docker-compose.deploy.yml" -ForegroundColor Cyan
    Copy-Item "../docker-compose.deploy.yml" "docker-compose.deploy.yml"
} else {
    Write-Host "📥 Downloading docker-compose.deploy.yml..." -ForegroundColor Yellow
    try {
        Invoke-WebRequest -Uri "https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/docker-compose.deploy.yml" -OutFile "docker-compose.deploy.yml" -ErrorAction Stop
    } catch {
        Write-Host "❌ Failed to download docker-compose.deploy.yml. Check your internet / URL and try again." -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "🚀 Starting the bot..." -ForegroundColor Yellow

# Stop and remove existing container if it exists (cleanup old manual runs)
try {
    docker stop telegram-coffee-bot 2>$null
    docker rm telegram-coffee-bot 2>$null
} catch {
    # Container doesn't exist, continue
}

# Check for docker compose command
$dockerComposeCmd = "docker-compose"
try {
    $null = docker compose version 2>$null
    if ($LASTEXITCODE -eq 0) { $dockerComposeCmd = "docker compose" }
} catch {
    # Fallback to docker-compose
}

Write-Host "Using $dockerComposeCmd..." -ForegroundColor Cyan
Invoke-Expression "$dockerComposeCmd -f docker-compose.deploy.yml pull"
Invoke-Expression "$dockerComposeCmd -f docker-compose.deploy.yml up -d"

# Validate and sanitize .env (trim whitespace and detect invalid keys)
if (Test-Path ".env") {
    $lines = Get-Content .env
    $sanitized = @(); $invalid = @(); $changed = $false
    foreach ($line in $lines) {
        if ($line.Trim() -eq '' -or $line.TrimStart().StartsWith('#')) { $sanitized += $line; continue }
        if ($line -notmatch '=') { $sanitized += $line; continue }
        $parts = $line -split '=',2; $k = $parts[0].Trim(); $v = $parts[1].Trim()
        if ($k -ne $parts[0] -or $v -ne $parts[1]) { $changed = $true }
        if ($k -match '\s') { $invalid += $k }
        $sanitized += "$k=$v"
    }
    if ($invalid.Count) { Write-Host "❌ Invalid .env keys with spaces: $($invalid -join ', ')" -ForegroundColor Red; exit 1 }
    if ($changed) { $ans = Read-Host "Trim whitespace in .env and save backup? [Y/n]"; if ([string]::IsNullOrWhiteSpace($ans)) { $ans = 'Y' }; if ($ans -match '^[Yy]$') { Copy-Item .env .env.bak; $sanitized -join "`n" | Set-Content .env; Write-Host '✅ .env sanitized' -ForegroundColor Green } }
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host "✅ Bot is now running with Watchtower (Auto-Updates)!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Container name: telegram_bot-coffee-bot-1 (or similar)" -ForegroundColor White
if ($setupMongo -match "^[Yy]$") {
    Write-Host "MongoDB container: telegram-coffee-mongodb" -ForegroundColor White
}
Write-Host "API endpoint: http://localhost:8000" -ForegroundColor White
Write-Host ""
Write-Host "Useful commands:" -ForegroundColor Cyan
Write-Host "  View bot logs:       $dockerComposeCmd -f docker-compose.deploy.yml logs -f coffee-bot" -ForegroundColor Gray
Write-Host "  Stop bot:            $dockerComposeCmd -f docker-compose.deploy.yml down" -ForegroundColor Gray
Write-Host "  Restart bot:         $dockerComposeCmd -f docker-compose.deploy.yml restart coffee-bot" -ForegroundColor Gray
if ($setupMongo -match "^[Yy]$") {
    Write-Host ""
    Write-Host "MongoDB commands:" -ForegroundColor Cyan
    Write-Host "  View MongoDB logs:   docker logs telegram-coffee-mongodb" -ForegroundColor Gray
    Write-Host "  Stop MongoDB:        docker stop telegram-coffee-mongodb" -ForegroundColor Gray
    Write-Host "  Start MongoDB:       docker start telegram-coffee-mongodb" -ForegroundColor Gray
    Write-Host "  Remove MongoDB:      docker rm -f telegram-coffee-mongodb" -ForegroundColor Gray
    Write-Host "  Remove MongoDB data: docker volume rm telegram-coffee-mongodb-data" -ForegroundColor Gray
}
Write-Host ""
