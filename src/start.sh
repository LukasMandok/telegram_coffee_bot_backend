# Script file to start gunicorn and processes
# Adapted from:  https://github.com/xstar97/reddit-auto-reply/blob/master/bot/start.sh

#!/bin/bash

# TODO: check, if this is actually needed
# # Start bot process in the background
# python3 bot.py &

# Save the process ID of the bot process
BOT_PID=$!

# Start web process in the foreground
gunicorn main:app --bind 0.0.0.0:3000 -w 4 -k uvicorn.workers.UvicornWorker

# Wait for the bot process to finish
wait $BOT_PID