# Code adapted from: https://github.com/xstar97/reddit-auto-reply/blob/master/Dockerfile

FROM python:3.13

# Set working dorectory for bot script
WORKDIR /app

# Copy all files from bot directory to container
COPY src/ /app/src
COPY tests/ /app/tests

# Install system dependencies
# Combine updates and installs to keep the image clean and avoid "debconf" errors
# Use --no-install-recommends to avoid installing huge unnecessary dependencies (like X11)
RUN apt-get update && \
    export DEBIAN_FRONTEND=noninteractive && \
    apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    libssl-dev \
    nodejs \
    npm && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip3 install --no-cache-dir -r /app/src/requirements.txt


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