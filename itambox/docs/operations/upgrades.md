# Upgrades

## Contract

1. **Back up first.** Always take a full database dump and copy your `.env` before upgrading. See [Backup & Restore](backup-restore.md).
2. Pull and rebuild.
3. Run migrations.
4. Collect static files.
5. Restart app + worker.

## Step-by-step

```bash
# 1. Back up (see backup-restore.md for the full pg_dump one-liner)
docker compose exec db pg_dump -U $ITAMBOX_DB_USER $ITAMBOX_DB_NAME > backup-$(date +%F).sql

# 2. Pull the new image (or git pull + rebuild for source installs)
docker compose pull
# — or for source installs —
git pull && docker compose build

# 3. Run database migrations
docker compose exec app python manage.py migrate

# 4. Collect static files
docker compose exec app python manage.py collectstatic --no-input

# 5. Restart
docker compose up -d
```

## Version skipping policy

!!! warning "Do not skip major versions"
    While ITAMbox is pre-1.0, every alpha/beta release may include breaking migrations. Always upgrade sequentially — never jump from `1.0.0-alpha1` directly to `1.0.0-alpha5`. Patch releases within the same minor (e.g. `1.0.1` → `1.0.2`) are always safe to apply in one step.

Once `v1.0.0` is tagged, the policy hardens: **never skip across major versions**. Upgrade 1.x → 2.x via the designated LTS bridge release documented in that version's upgrade notes.

## Rollback

If a migration fails mid-upgrade:

```bash
# Roll back the last migration batch
docker compose exec app python manage.py migrate <app_label> <previous_migration>

# Restore the DB from your pre-upgrade backup if needed
docker compose exec -T db psql -U $ITAMBOX_DB_USER $ITAMBOX_DB_NAME < backup-YYYY-MM-DD.sql
```

Check `manage.py showmigrations` to identify the last applied migration before the failure.

## Checking the running version

The current version is exposed at the `/api/status/` endpoint:

```bash
curl -s http://localhost:8000/api/status/ | python -m json.tool
```

It also appears in the UI footer and on the login page.
