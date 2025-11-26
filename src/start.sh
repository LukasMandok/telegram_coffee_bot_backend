#!/bin/bash
# Script file to start gunicorn and processes
# Adapted from: https://github.com/xstar97/reddit-auto-reply/blob/master/bot/start.sh

cd /app

# Handle MongoDB Host for Docker
# If MONGO_HOST is set to localhost or 127.0.0.1, it means the user intends to connect
# to the MongoDB on the host machine (since this is running inside a container).
# In Docker, we must use host.docker.internal to reach the host.
if [ "$MONGO_HOST" = "localhost" ] || [ "$MONGO_HOST" = "127.0.0.1" ]; then
    echo "--- Adjusting MONGO_HOST from '$MONGO_HOST' to 'host.docker.internal' for Docker environment ---"
    export MONGO_HOST="host.docker.internal"
fi

# Check if DEBUG_MODE is set to true (case insensitive)
# Use tr for POSIX compatibility since this runs with /bin/sh
DEBUG_LOWER=$(echo "$DEBUG_MODE" | tr '[:upper:]' '[:lower:]')

if [ "$DEBUG_LOWER" = "true" ]; then
    echo "--- Starting in DEBUG mode ---"
    export PYTHONPATH=/app
    # Start with debugpy, listening on port 5678
    python -Xfrozen_modules=off -m debugpy --listen 0.0.0.0:5678 -m src.main
else
    echo "--- Starting in PRODUCTION mode ---"
    # Start web process in the foreground using gunicorn
    # Note: Changed port to 8000 to match docker-compose.yml
    gunicorn src.main:app --bind 0.0.0.0:8000 -w 4 -k uvicorn.workers.UvicornWorker
fi