# Code adapted from: https://github.com/xstar97/reddit-auto-reply/blob/master/Dockerfile

FROM python:3.13

# Set working dorectory for bot script
WORKDIR /app

# Copy all files from bot directory to container
COPY src/ /app/src

# Copy .env file to the container
COPY .env /app/.env

# Install system dependencies
RUN apt update && \
    apt install -y build-essential libffi-dev libssl-dev

# Install Python dependencies
RUN pip3 install --no-cache-dir -r /app/src/requirements.txt

# Install Node.js and npm
RUN apt install -y nodejs npm

# Set the environment variables for the bot
# TODO: Custom environment variables
ENV BOT_TOKEN ${BOT_TOKEN}
ENV BOT_HOST ${BOT_HOST}

# Set user group as environment variables
ENV PUID=1000
ENV PGID=1000

# Create a non-root user with the given user and group IDs
RUN groupadd -g $PGID cof && \
    useradd -u $PUID -g cof -m cof


# Change the ownership of the working directory and start script to the non-root user
RUN chown -R cof:cof /app
RUN chown cof:cof src/start.sh
RUN chmod +x src/start.sh

# Set the non-root user as the user to run the container
USER cof

# Run the start script when the container launches
CMD ["sh", "src/start.sh"]