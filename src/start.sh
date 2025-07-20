#!/bin/bash
# Script file to start gunicorn and processes
# Adapted from: https://github.com/xstar97/reddit-auto-reply/blob/master/bot/start.sh

cd /app

# Check if DEBUG_MODE is set to true
if [ "$DEBUG_MODE" = "true" ]; then
    echo "--- Starting in DEBUG mode ---"
    export PYTHONPATH=/app
    # Start with debugpy, listening on port 5678
    python -Xfrozen_modules=off -m debugpy --listen 0.0.0.0:5678 --wait-for-client -m src.main
else
    echo "--- Starting in PRODUCTION mode ---"
    # Start web process in the foreground using gunicorn
    # Note: Changed port to 8000 to match docker-compose.yml
    gunicorn src.main:app --bind 0.0.0.0:8000 -w 4 -k uvicorn.workers.UvicornWorker
fi