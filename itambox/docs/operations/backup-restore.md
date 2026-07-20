# Backup and restore

## State that must travel together

| Component | Contents | Backup method |
|---|---|---|
| PostgreSQL | Application records, identities, authorization, and change history | Transaction-consistent `pg_dump` |
| `media` volume | Uploaded attachments | Volume snapshot or file copy |
| `.env` or external secret store | Database credentials, Django secret, encryption keyring, and API-token peppers | Encrypted secret export |

!!! danger "The field-encryption keyring is irreplaceable"
    Production should use a dedicated `ITAMBOX_FIELD_ENCRYPTION_KEYS` keyring. Back up every active and retired decryption key in its original order. If the variable was never configured, encrypted fields instead depend on `ITAMBOX_SECRET_KEY`; preserve that key as well. Losing the applicable key makes encrypted SMTP passwords, license keys, and webhook secrets unreadable.

`ITAMBOX_API_TOKEN_PEPPERS` is separate from field encryption. Losing it invalidates existing API tokens. Preserve all rotation IDs still used by issued tokens.

## Compose backup

For a point-in-time set that keeps database, media, and secrets aligned, pause writers first:

```bash
set -Eeuo pipefail
umask 077
stamp=$(date +%F-%H%M%S)
backup_dir="itambox-backup-${stamp}"
mkdir -m 700 "$backup_dir"
dump="${backup_dir}/database.dump"
dump_partial="${dump}.partial"
media_dir="${backup_dir}/media"
media_archive="${backup_dir}/media.tar.gz"
env_backup="${backup_dir}/environment"
backup_complete=false
writers_paused=true

cleanup_backup() {
  rc=$?
  trap - EXIT
  if [[ "$backup_complete" == false ]]; then
    rm -rf "$backup_dir"
  else
    rm -f "$dump_partial"
    rm -rf "$media_dir"
  fi
  if [[ "$writers_paused" == true ]]; then
    if ! docker compose start app worker; then
      echo "Backup failed and writers could not be restarted; intervene immediately." >&2
      ((rc == 0)) && rc=1
    fi
  fi
  exit "$rc"
}
trap cleanup_backup EXIT

docker compose stop app worker

docker compose exec -T db sh -c \
  'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' \
  > "$dump_partial"
test -s "$dump_partial"
mv "$dump_partial" "$dump"

docker compose cp app:/app/media "./${media_dir}"
tar -C "./${media_dir}" -czf "$media_archive" .
rm -rf "./${media_dir}"
cp .env "$env_backup"
(
  cd "$backup_dir"
  sha256sum database.dump media.tar.gz environment > SHA256SUMS.partial
  test -s SHA256SUMS.partial
  mv SHA256SUMS.partial SHA256SUMS
)
backup_complete=true

docker compose start app worker
writers_paused=false
```

The restrictive `umask` protects newly created local files from other users, but it is not encryption. Encrypt the backup set before moving it off-host. If production secrets come from an external manager, export the exact deployed versions instead of copying an incomplete local file.

A database-only `pg_dump` can run while the application is online, but it does not create a point-in-time snapshot of uploaded media. Use storage snapshots or a maintenance window when database rows and files must be restored as one set.

For an external PostgreSQL service, run `pg_dump` with its host, port, database, and user parameters rather than through the `db` service.

## Restore procedure

Restore into an isolated environment first whenever possible. Use the same application revision and the secret set captured with the backup.

```bash
set -Eeuo pipefail
BACKUP_DIR='itambox-backup-YYYY-MM-DD-HHMMSS'
BACKUP_REVISION='full-backed-up-commit-sha'
media_restore_dir=''
writers_stopped=false

cleanup_restore() {
  rc=$?
  trap - EXIT
  if [[ -n "$media_restore_dir" ]]; then
    rm -rf "$media_restore_dir"
  fi
  if ((rc != 0)) && [[ "$writers_stopped" == true ]]; then
    echo "Restore failed after writers were stopped. Do not admit traffic; inspect the stack and restore logs." >&2
  fi
  exit "$rc"
}
trap cleanup_restore EXIT

# 1. Verify the set and select the application revision associated with it.
(cd "$BACKUP_DIR" && sha256sum -c SHA256SUMS)
git checkout --detach "$BACKUP_REVISION"

# 2. Restore the matching secrets before the first Compose command, then build.
cp "${BACKUP_DIR}/environment" .env
docker compose build

# 3. Stop writers and ensure only infrastructure services are running.
writers_stopped=true
docker compose stop app worker
docker compose up -d db valkey
docker compose create --force-recreate app worker

# 4. Recreate and restore the database from a custom-format dump.
docker compose exec -T db sh -c \
  'dropdb -U "$POSTGRES_USER" --if-exists "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
docker compose exec -T db sh -c \
  'pg_restore -U "$POSTGRES_USER" --no-owner -d "$POSTGRES_DB"' \
  < "${BACKUP_DIR}/database.dump"

# 5. Restore the matching media snapshot into an empty media volume.
media_restore_dir=$(mktemp -d -p . itambox-media-restore-XXXXXX)
tar -xzf "${BACKUP_DIR}/media.tar.gz" \
  -C "$media_restore_dir"
docker compose cp "${media_restore_dir}/." app:/app/media/
docker compose run --rm --no-deps --user root app \
  chown -R appuser:appuser /app/media

# 6. Start the application and worker.
docker compose up -d
writers_stopped=false
```

If reusing a media volume, remove files that are not part of the backup with your volume-management tooling before copying the snapshot. For plain SQL dumps, replace `pg_restore` with `psql -U "$POSTGRES_USER" "$POSTGRES_DB"` inside the `db` container.

## Verification

Exercise restores regularly in a non-production environment. Verify at least:

- application startup, health endpoint, and login;
- representative assets, assignments, change records, and attachments;
- decryption of SMTP, license, and webhook fields;
- an API token created before the backup;
- migration state with `python manage.py showmigrations --plan`.

Choose backup frequency, retention, off-site copies, and recovery objectives from your own risk and compliance requirements; the repository does not define a universal production SLA.
