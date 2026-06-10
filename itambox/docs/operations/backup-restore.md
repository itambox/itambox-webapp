# Backup & Restore

## What holds state

| Component | What's in it | How to back it up |
|---|---|---|
| **PostgreSQL database** | All application data — assets, tenants, users, change logs | `pg_dump` (see below) |
| **`media/` volume** | Uploaded file attachments | Copy or snapshot the volume |
| **`.env` / `ITAMBOX_SECRET_KEY`** | Encryption master key + DB credentials | Copy `.env` alongside every DB dump |

!!! danger "Secret key is irreplaceable"
    ITAMbox encrypts sensitive fields (e.g. SMTP passwords stored in `EmailSettings`) using a Fernet key derived from `ITAMBOX_SECRET_KEY`. **If you lose `SECRET_KEY`, all encrypted values become permanently unreadable.** There is no recovery path. Store `.env` in a secrets manager or an encrypted off-site backup — and treat it as part of the database backup, not as a config file.

## Database backup

### Docker Compose (standard install)

```bash
docker compose exec db pg_dump \
  -U $ITAMBOX_DB_USER \
  $ITAMBOX_DB_NAME \
  > backup-$(date +%F-%H%M).sql
```

For compressed dumps:

```bash
docker compose exec db pg_dump \
  -U $ITAMBOX_DB_USER \
  -Fc $ITAMBOX_DB_NAME \
  > backup-$(date +%F-%H%M).dump
```

### Bare-metal install

```bash
pg_dump -h $ITAMBOX_DB_HOST -p $ITAMBOX_DB_PORT \
  -U $ITAMBOX_DB_USER $ITAMBOX_DB_NAME \
  > backup-$(date +%F-%H%M).sql
```

## Media volume backup

```bash
docker compose cp app:/app/media ./media-backup-$(date +%F)
```

Or snapshot the Docker volume directly with your infrastructure tooling.

## Restore procedure

```bash
# 1. Stop the application
docker compose stop app worker

# 2. Restore the database (plain SQL dump)
docker compose exec -T db psql \
  -U $ITAMBOX_DB_USER \
  $ITAMBOX_DB_NAME \
  < backup-YYYY-MM-DD.sql

# For compressed dumps:
docker compose exec -T db pg_restore \
  -U $ITAMBOX_DB_USER \
  -d $ITAMBOX_DB_NAME \
  < backup-YYYY-MM-DD.dump

# 3. Restore media files
docker compose cp ./media-backup-YYYY-MM-DD/. app:/app/media/

# 4. Ensure .env is correct (SECRET_KEY must match the backup)
# 5. Restart
docker compose up -d
```

## Backup frequency recommendation

| Data class | Recommended cadence |
|---|---|
| Production database | Daily automated dump; retain 30 days |
| Media volume | Weekly snapshot |
| `.env` / secret key | Store once in a secrets manager; update when rotated |

## Quarterly restore test

!!! tip "Test your backups"
    A backup you have never restored is a backup you cannot trust. Run a restore drill into a staging environment at least once per quarter. Verify:

    - App starts and login works.
    - A sample asset record is visible and correct.
    - Encrypted fields (e.g. email notification settings) decrypt without error.
