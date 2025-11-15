# MongoDB Management Scripts

## Overview

This directory contains scripts for managing the Telegram Coffee Bot MongoDB instance.

## Configuration

- **Container Name:** telegram-coffee-mongodb
- **Port:** {{MONGO_PORT}}
- **Database:** {{MONGO_DATABASE}}
- **Backup Directory:** {{BACKUP_DIR}}
- **Retention Period:** {{RETENTION_DAYS}} days
- **Backup Time:** Daily at {{BACKUP_TIME}}

## Scripts

### mongo_backup.sh / mongo_backup.ps1
Performs a full MongoDB backup and saves it to the backup directory.

**Usage:**
```bash
# Linux/Mac
{{BACKUP_SCRIPT}}

# Windows
{{BACKUP_SCRIPT}}
```

**Automated:** Runs daily at {{BACKUP_TIME}} via cron/scheduled task

### mongo_restore.sh / mongo_restore.ps1
Restores MongoDB from a backup file.

**Usage:**
```bash
# Linux/Mac
{{RESTORE_SCRIPT}} /path/to/backup.archive

# Windows
{{RESTORE_SCRIPT}} -BackupFile "path\to\backup.archive"
```

**List available backups:**
```bash
# Linux/Mac
ls -lh {{BACKUP_DIR}}/

# Windows
Get-ChildItem "{{BACKUP_DIR}}"
```

## Useful Commands

### View MongoDB logs
```bash
docker logs telegram-coffee-mongodb
docker logs -f telegram-coffee-mongodb  # follow logs
```

### Connect to MongoDB shell
```bash
docker exec -it telegram-coffee-mongodb mongosh -u {{MONGO_USERNAME}} -p {{MONGO_PASSWORD}} --authenticationDatabase admin
```

### Stop/Start MongoDB
```bash
docker stop telegram-coffee-mongodb
docker start telegram-coffee-mongodb
docker restart telegram-coffee-mongodb
```

### View backup logs
```bash
# Linux/Mac
cat {{SCRIPTS_DIR}}/mongo_backup.log
tail -f {{SCRIPTS_DIR}}/mongo_backup.log  # follow logs

# Windows
Get-Content "{{SCRIPTS_DIR}}\mongo_backup.log"
Get-Content "{{SCRIPTS_DIR}}\mongo_backup.log" -Wait  # follow logs
```

### Manual backup
```bash
{{BACKUP_SCRIPT}}
```

### Manage automation

**Linux/Mac (cron):**
```bash
crontab -l  # list cron jobs
crontab -e  # edit cron jobs
```

**Windows (Task Scheduler):**
```powershell
Get-ScheduledTask -TaskName "TelegramCoffeeBotMongoBackup"
# Or open Task Scheduler GUI
taskschd.msc
```

## MongoDB Connection String

```
mongodb://{{MONGO_USERNAME}}:{{MONGO_PASSWORD}}@localhost:{{MONGO_PORT}}/{{MONGO_DATABASE}}
```

## Notes

- Backups are stored in `{{BACKUP_DIR}}`
- Old backups are automatically deleted after {{RETENTION_DAYS}} days
- The MongoDB data is stored in a Docker volume: `telegram-coffee-mongodb-data`
- To completely remove MongoDB and all data: `docker rm -f telegram-coffee-mongodb && docker volume rm telegram-coffee-mongodb-data`

---

**Created:** {{CREATED_DATE}}
**Author:** Telegram Coffee Bot Setup Script
