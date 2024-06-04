# Code adapted from: https://github.com/xstar97/reddit-auto-reply/blob/master/Dockerfile

FROM python:3.11

# Set working dorectory for bot script
WORKDIR /config

# Copy all files from bot directory to container
COPY src/ /config/

# Copy .env file to the container
COPY .env /config/.env

# Install system dependencies
RUN apt update && \
    apt install -y build-essential libffi-dev libssl-dev

# Install Python dependencies
RUN pip3 install --no-cache-dir -r requirements.txt

# Install Node.js and npm
RUN apt install -y nodejs npm

# Set the environment variables for the bot
# TODO: Custom environment variables
ENV TELEGRAM_TOKEN ${TELEGRAM_TOKEN}
ENV BOT_HOST ${BOT_HOST}

# Set user group as environment variables
ENV PUID=1000
ENV PGID=1000

# Create a non-root user with the given user and group IDs
RUN addgroup -g $PGID cof && \
    adduser -D -u $PUID -G cof cof


# Change the ownership of the working directory and start script to the non-root user
RUN chown -R cof:cof /config
RUN chown cof:cof /config/start.sh
RUN chmod +x /config/start.sh

# Set the non-root user as the user to run the container
USER cof

# Run the start script when the container launches
CMD ["sh", "/config/start.sh"]