# Updating a deployment

ITAMbox is pre-release and currently ships as source, not as a published container image. There is no compatibility or version-skipping guarantee yet. Treat every target revision as a potentially breaking change and test it against a restored copy of production first.

Releases that change migrations, storage, encryption, or other recovery-critical
behavior must also pass the isolated [Recovery qualification drill](recovery-drill.md).
That runbook proves predecessor restore, candidate upgrade, fresh-install schema
parity, restore-first rollback, and re-upgrade without exposing protected values.

## OIDC identity binding migration (#454)

The OIDC `(issuer, subject)` binding migration creates schema only. It performs no automatic backfill and never guesses a binding from email, username, UPN, tenant,
or an existing AssetHolder. Existing OIDC users therefore require an explicit
`bind_oidc_identity` operation before they can use the new login path. Until that
binding exists, legacy logins fail closed; they do not silently fall back to the
predecessor resolver.

### Forward deployment order

1. Export and protect any operator-approved binding plan separately from the
   database backup. Do not derive one automatically from mutable claims.
2. Apply the candidate schema migration (`users.0103_oidcidentity`) while the
   predecessor application is still stopped or controlled. The predecessor does
   not use the new table.
3. Deploy the candidate application code only after the schema migration has
   completed successfully.
4. Validate the exact configured issuer and subject, then run the explicit
   `bind_oidc_identity --confirm` command for each approved User. Use `--dry-run`
   first and keep the command output sanitized.
5. Admit OIDC traffic only after the required bindings and a login verification
   have been completed. Unbound legacy users continue to fail closed.

### Rollback and irreversible data loss

Reversing `users.0103_oidcidentity` permanently drops every binding created after
this migration. Reverse does not restore, reconstruct, or preserve those rows;
export them first if they must be recreated. A database backup is the preferred
rollback source. Without an export or backup, all bindings must be manually
recreated through the explicit command after a later forward deployment.

If a schema reverse is unavoidable, stop writers, roll back the application code
to the predecessor first, verify that no candidate code can query
`users_oidcidentity`, and only then reverse the migration. Never leave candidate
code running against a database in which the binding table has already been
dropped. A schema reverse while candidate code is active causes missing-table
failures; a code rollback before schema reverse leaves the table unused but
available for recovery.

The predecessor code is not a security-equivalent rollback: once it is serving,
its email/username claim resolution behavior is reopened. That can relink a
login through mutable claims and is the security consequence of rolling back the
binding code. Treat this as a temporary security regression, keep OIDC traffic
blocked unless explicitly accepted, and redeploy the binding code/schema in the
forward order above as soon as possible.

## Preflight

1. Select and review an exact target commit.
2. Review the repository-root `CHANGELOG.md`, migrations, and configuration changes between the deployed and target commits.
3. Capture the current revision with `git rev-parse HEAD` and retain or export the currently running application image; rebuilding an old commit later may resolve newer base images or dependency versions.
4. Take a complete [database, media, and secret backup](backup-restore.md) and verify that it can be read.
5. Plan a maintenance window; prerelease migrations are not guaranteed to be compatible with the old application.

## Source-built Compose update

```bash
set -Eeuo pipefail
writers_stopped=false

report_failed_upgrade() {
  rc=$?
  trap - EXIT
  if ((rc != 0)) && [[ "$writers_stopped" == true ]]; then
    echo "Upgrade failed after writers were stopped. Do not admit traffic; inspect the stack and follow the rollback plan." >&2
  fi
  exit "$rc"
}
trap report_failed_upgrade EXIT

# Record the rollback revision before changing the checkout.
ROLLBACK_REVISION=$(git rev-parse HEAD)
printf 'rollback revision: %s\n' "$ROLLBACK_REVISION"

# Fetch and select the reviewed target revision explicitly.
git fetch origin
TARGET_REVISION='full-reviewed-target-commit-sha'
git checkout --detach "$TARGET_REVISION"

# Build the new application and frontend assets from source.
docker compose build --pull

# Stop writers, migrate with the new image, and restart.
writers_stopped=true
docker compose stop app worker
docker compose run --rm app python manage.py migrate
docker compose up -d
writers_stopped=false

# Inspect startup after the deployment.
docker compose ps
docker compose logs --tail=100 app worker
```

`collectstatic` runs while the application image is built, so the included stack does not need a separate post-deployment collection step.

## Post-upgrade notes

Event rule conditions are withdrawn for the 1.0 release. Existing condition JSON
is preserved and remains readable, but rules with authored conditions will not
dispatch. After upgrading, identify affected active rules with:

```bash
python manage.py eventrule_withdrawn_report
```

## Rollback

Do not assume a Django migration can be reversed safely. A migration may be irreversible or may discard data when reversed. The reliable rollback is the complete pre-update set:

1. Stop the application and worker.
2. Restore the retained previous image, or check out and rebuild the recorded previous revision. A rebuild is not necessarily byte-identical when base images or ranged dependencies have moved.
3. Restore the matching database, media, and secrets using [Backup and restore](backup-restore.md).
4. Start the prior application and verify login, attachments, encrypted fields, and background processing.

If an update fails before any migration runs, returning to the previous source revision and rebuilding may be sufficient. Once a migration starts, use the tested backup-based rollback unless that specific migration has been reviewed and proven reversible.

## Identifying the deployed revision

`/api/status/`, the login page, and the application footer report ITAMbox version metadata. That value identifies the declared software version (currently prerelease metadata), not the exact deployed Git commit. Use it as a sanity check and record the source revision separately in the deployment system:

```bash
curl -fsS https://itam.example.com/api/status/
git rev-parse HEAD
git status --short
docker compose images
```

Keep the checkout clean and pin the recorded commit so a later rebuild uses the same source.
