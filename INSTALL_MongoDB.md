# MongoDB Docker Setup on Debian (Armbian / Raspberry Pi)

This guide describes how to install and run a MongoDB instance in Docker, together with an automated backup routine.

---

## 📁 Directory structure

```
/home/labor/
├── mongodb-docker/      # Docker Compose project
│   └── docker-compose.yml
├── mongodb-backups/     # Backup folder (daily dumps stored here)
└── scripts/
    └── mongo_backup.sh  # Backup script
```

---

## 🐳 1. Install Docker & Docker Compose

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
```

Add your user to the `docker` group to run containers without `sudo`:

```bash
sudo usermod -aG docker labor
newgrp docker
```

Check that Docker works:
```bash
docker ps
```

---

## 🧩 2. MongoDB Docker setup

Create the project folder and add the Compose file:

```bash
mkdir -p ~/mongodb-docker
cd ~/mongodb-docker
```

`docker-compose.yml`:

```yaml
services:
  mongodb:
    image: mongodb/mongodb-community-server
    container_name: mongodb
    ports:
      - 27017:27017
    volumes:
      - mongodb_data:/data/db
    environment:
      - MONGO_INITDB_ROOT_USERNAME=admin
      - MONGO_INITDB_ROOT_PASSWORD=password
    restart: unless-stopped

volumes:
  mongodb_data:
    driver: local
```

### Start MongoDB

```bash
docker compose up -d
```

### Check status

```bash
docker ps
```

You should see a container named `mongodb` running on port `27017`.

---

## 💾 3. Backup script

Create the scripts folder and the backup script:

```bash
mkdir -p ~/scripts
vim ~/scripts/mongo_backup.sh
chmod +x ~/scripts/mongo_backup.sh
```

`mongo_backup.sh`:

```bash
#!/bin/bash
# mongo_backup.sh — daily MongoDB dump with rotation (keeps 30 days)

BACKUP_DIR="/home/labor/mongodb-backups"
DATE=$(date +%F)
CONTAINER_NAME="mongodb"
USER="admin"
PASS="password"
PORT=27017

mkdir -p "$BACKUP_DIR"

docker exec "$CONTAINER_NAME" mongodump \
  --username "$USER" \
  --password "$PASS" \
  --authenticationDatabase admin \
  --port "$PORT" \
  --archive=/tmp/mongo_backup_$DATE.archive \
  --gzip

docker cp "$CONTAINER_NAME:/tmp/mongo_backup_$DATE.archive" "$BACKUP_DIR/mongo_backup_$DATE.archive"

docker exec "$CONTAINER_NAME" rm "/tmp/mongo_backup_$DATE.archive"

find "$BACKUP_DIR" -type f -mtime +30 -name "mongo_backup_*.archive" -delete
```

---

## ⏰ 4. Automate backups with cron

Edit your user’s crontab:

```bash
crontab -e
```

Add this line to run the backup every night at 03:00:

```
0 3 * * * /home/labor/scripts/mongo_backup.sh >> /home/labor/mongo_backup.log 2>&1
```

---

## 🔁 5. Restore a backup

To restore a specific archive:

```bash
docker exec -i mongodb mongorestore \
  --username admin \
  --password password \
  --authenticationDatabase admin \
  --archive \
  --gzip < /home/labor/mongodb-backups/mongo_backup_2025-11-08.archive
```

---

## 🧹 6. Maintenance commands

Stop MongoDB:
```bash
docker compose down
```

Remove all containers and volumes (⚠ deletes all data):
```bash
docker compose down -v
```

List backups:
```bash
ls -lh ~/mongodb-backups
```

Check logs:
```bash
docker logs mongodb
```

---

## ✅ Notes

- The `mongodb_data` Docker volume keeps your database data persistent.
- Backups are stored for 30 days under `/home/labor/mongodb-backups/`.
- make sure to adjust the password for the mongodb credentials everywhere.
  **user:** `admin`  
  **password:** `password`
- Adjust the cron time or retention period as you like.

---

**Author:** Lukas Mandok  
**Platform:** Raspberry Pi 5, Armbian Trixie  
**Purpose:** Local MongoDB for Telegram Coffee Bot