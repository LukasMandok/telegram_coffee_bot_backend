# Telegram Coffee Bot - Quick Setup Script (PowerShell)
# This script downloads the latest Docker image and sets up the environment

$ErrorActionPreference = "Stop"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Telegram Coffee Bot - Quick Setup" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

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

    # Extract connection string from the handshake file if it exists
    $connectionLine = $null
    if (Test-Path $handshake) { $connectionLine = Get-Content $handshake | Where-Object { $_ -match "MONGODB_CONNECTION=" } }
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
    Write-Host "📥 Downloading .env.example template..." -ForegroundColor Yellow
    try {
        Invoke-WebRequest -Uri "https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/.env.example" -OutFile ".env.example" -ErrorAction Stop
    } catch {
        Write-Host "❌ Failed to download .env.example. Check your internet / URL and try again." -ForegroundColor Red
        exit 1
    }
    
    Write-Host "📝 Creating .env file from template..." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
    
    # Set the DATABASE_URL in .env
    $envContent = Get-Content ".env"
    # Update DATABASE_URL safely without regex replacement quirks
    $envContent = $envContent | ForEach-Object { if ($_ -match '^DATABASE_URL=.*') { "DATABASE_URL=$dbUrl" } else { $_ } }
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
    Write-Host "The DATABASE_URL has been set to: $dbUrl" -ForegroundColor Gray
    Write-Host ""
    
    # Open .env in default editor; prefer VS Code (code) if installed
    Write-Host "Opening .env file in default editor..." -ForegroundColor Yellow
    try {
        if (Get-Command code -ErrorAction SilentlyContinue) {
            Start-Process code -ArgumentList '--wait', '.env'
        } else {
            Start-Process notepad.exe ".env"
        }
    } catch {
        Write-Host "Could not open an editor automatically. Please edit .env manually: $(Join-Path (Get-Location) '.env')" -ForegroundColor Yellow
    }
    
    Read-Host "Press Enter after you've edited and saved the .env file"
} else {
    Write-Host "✅ .env file already exists" -ForegroundColor Green
    # If a MongoDB setup was performed, offer to update the DATABASE_URL in the existing .env
    if ($setupMongo -match "^[Yy]$") {
        $updateDbUrl = Read-Host "Do you want to update DATABASE_URL in the existing .env to the generated MongoDB connection? [Y/n]"
        if ([string]::IsNullOrWhiteSpace($updateDbUrl)) { $updateDbUrl = "Y" }
        if ($updateDbUrl -match "^[Yy]$") {
            try {
                $envContent = Get-Content ".env"
                $envContent = $envContent -replace "DATABASE_URL=.*", "DATABASE_URL=$dbUrl"
                $envContent | Set-Content ".env"
                Write-Host "✅ DATABASE_URL updated in .env" -ForegroundColor Green
            } catch {
                Write-Host "⚠️ Could not update .env automatically, please edit it manually." -ForegroundColor Yellow
            }
        }
    }
    $editEnv = Read-Host "Do you want to edit .env now? [y/N]"
    if ([string]::IsNullOrWhiteSpace($editEnv)) { $editEnv = "n" }
    if ($editEnv -match "^[Yy]$") {
        Write-Host "Opening .env in default editor..." -ForegroundColor Yellow
        try {
            if (Get-Command code -ErrorAction SilentlyContinue) {
                Start-Process code -ArgumentList '--wait', '.env'
            } else {
                Start-Process notepad.exe ".env"
            }
        } catch {
            Write-Host "Could not open Notepad automatically. Please edit .env manually: $(Join-Path (Get-Location) '.env')" -ForegroundColor Yellow
        }
        Read-Host "Press Enter after you've edited and saved the .env file"
    }
} 

Write-Host ""
Write-Host "🐳 Pulling latest Docker image from GitHub Container Registry..." -ForegroundColor Yellow
docker pull ghcr.io/lukasmandok/telegram_coffee_bot_backend:main

Write-Host ""
Write-Host "🚀 Starting the bot..." -ForegroundColor Yellow

# Stop and remove existing container if it exists
try {
    docker stop telegram-coffee-bot 2>$null
    docker rm telegram-coffee-bot 2>$null
} catch {
    # Container doesn't exist, continue
}

docker run -d `
  --name telegram-coffee-bot `
  --env-file .env `
  --restart unless-stopped `
  -p 8000:8000 `
  -v "${PWD}/src:/app/src" `
  ghcr.io/lukasmandok/telegram_coffee_bot_backend:main

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
Write-Host "✅ Bot is now running!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Container name: telegram-coffee-bot" -ForegroundColor White
if ($setupMongo -match "^[Yy]$") {
    Write-Host "MongoDB container: telegram-coffee-mongodb" -ForegroundColor White
}
Write-Host "API endpoint: http://localhost:8000" -ForegroundColor White
Write-Host ""
Write-Host "Useful commands:" -ForegroundColor Cyan
Write-Host "  View bot logs:       docker logs telegram-coffee-bot" -ForegroundColor Gray
Write-Host "  Follow bot logs:     docker logs -f telegram-coffee-bot" -ForegroundColor Gray
Write-Host "  Stop bot:            docker stop telegram-coffee-bot" -ForegroundColor Gray
Write-Host "  Start bot:           docker start telegram-coffee-bot" -ForegroundColor Gray
Write-Host "  Restart bot:         docker restart telegram-coffee-bot" -ForegroundColor Gray
Write-Host "  Remove bot:          docker rm -f telegram-coffee-bot" -ForegroundColor Gray
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
