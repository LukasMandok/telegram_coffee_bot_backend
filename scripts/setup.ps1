# Telegram Coffee Bot - Quick Setup Script (PowerShell)
# This script downloads the latest Docker image and sets up the environment

$ErrorActionPreference = "Stop"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Telegram Coffee Bot - Quick Setup" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

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
$setupMongo = Read-Host "Setup MongoDB? [y/N]"
if ([string]::IsNullOrWhiteSpace($setupMongo)) {
    $setupMongo = "n"
}

if ($setupMongo -match "^[Yy]$") {
    Write-Host ""
    Write-Host "📥 Downloading MongoDB setup script..." -ForegroundColor Yellow
    
    # Download setup_mongodb.ps1 from GitHub
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/setup_mongodb.ps1" -OutFile "setup_mongodb_temp.ps1"
    
    # Run MongoDB setup script and capture output
    $mongoOutput = & powershell.exe -ExecutionPolicy Bypass -File ".\setup_mongodb_temp.ps1"
    
    # Extract connection string from output
    $connectionLine = $mongoOutput | Where-Object { $_ -match "MONGODB_CONNECTION=" }
    if ($connectionLine) {
        $dbUrl = ($connectionLine -split "=", 2)[1]
    } else {
        # Fallback to default if connection string not found
        $dbUrl = "mongodb://admin:password123@localhost:27017/fastapi"
    }
    
    # Clean up temporary script
    Remove-Item "setup_mongodb_temp.ps1" -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "⏭️  Skipping MongoDB setup" -ForegroundColor Yellow
    Write-Host ""
    $dbUrl = "mongodb://admin:password123@localhost:27017/fastapi"
}

# Download .env.example if .env doesn't exist
if (-not (Test-Path ".env")) {
    Write-Host "📥 Downloading .env.example template..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/LukasMandok/telegram_coffee_bot_backend/main/.env.example" -OutFile ".env.example"
    
    Write-Host "📝 Creating .env file from template..." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
    
    # Set the DATABASE_URL in .env
    $envContent = Get-Content ".env"
    $envContent = $envContent -replace "DATABASE_URL=.*", "DATABASE_URL=$dbUrl"
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
    
    # Open .env in default editor
    Write-Host "Opening .env file in default editor..." -ForegroundColor Yellow
    Start-Process notepad.exe ".env"
    
    Read-Host "Press Enter after you've edited and saved the .env file"
} else {
    Write-Host "✅ .env file already exists" -ForegroundColor Green
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
  ghcr.io/lukasmandok/telegram_coffee_bot_backend:main

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
