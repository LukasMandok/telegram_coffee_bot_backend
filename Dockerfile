# Code adapted from: https://github.com/xstar97/reddit-auto-reply/blob/master/Dockerfile

FROM python:3.13

# 1. System-Abhängigkeiten zuerst installieren (ändert sich fast nie -> perfekter Cache)
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

# Setze das Arbeitsverzeichnis
WORKDIR /app

# 2. Python-Requirements separat kopieren und installieren (nutzt Cache, solange sich keine Library ändert)
COPY src/requirements.txt ./src/requirements.txt
RUN pip3 install --no-cache-dir -r ./src/requirements.txt

# 3. Den restlichen Code und die CHANGELOG kopieren (ändert sich oft)
COPY src/ ./src/
COPY tests/ ./tests/
COPY CHANGELOG.md ./CHANGELOG.md

# Set user group as environment variables
ENV PUID=1000
ENV PGID=1000

# Create a non-root user with the given user and group IDs
RUN groupadd -g $PGID cof && \
    useradd -u $PUID -g cof -m cof

# Berechtigungen anpassen (Pfade korrigiert, da wir im WORKDIR /app sind)
RUN chown -R cof:cof /app && \
    chmod +x src/start.sh

# Set the non-root user as the user to run the container
USER cof

# Run the start script when the container launches
CMD ["sh", "src/start.sh"]