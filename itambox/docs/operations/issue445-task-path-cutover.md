# Issue #445 task-path cutover: deployment runbook

Applies the reviewed django-q task-path ownership cutover
(`extras.0110_issue445_task_paths`) to a live deployment. The migration
rewrites only `django_q.Schedule.func` for the twelve canonical predecessor
paths; every other schedule field, PK and row multiplicity is preserved.

All commands are guaranteed against the repository's own verification tooling.
They contain no secrets; run them from a shell that already holds the deployed
environment (secrets stay in `.env` / the external secret store).

## Snapshot and restore (execute BEFORE any cutover step)

Use the site-supported backup procedure from `backup-restore.md` verbatim:
transaction-consistent `pg_dump -Fc` of the application database, the `media`
volume, and the secret set, with size/format verification and a hash manifest.
The django-q ORM broker lives in the same database, so the dump is also the
durable queue snapshot; a Redis/Valkey broker would additionally require its
`django_q:{cluster}:*` list exports before the drain step.

Verify the snapshot before proceeding:

```bash
# from backup-restore.md, after the dump step:
sha256sum "$dump" "$media_archive" "$env_backup" > "$manifest"
pg_restore --list "$dump" | head -20   # format/schema sanity, no restore
```

Restore drill (isolated environment): `backup-restore.md` → *Restore
procedure*, then `manage.py migrate --plan` must show the exact applied head
and `manage.py check` must pass before the cutover continues.

## Forward cutover

1. **Freeze producers and scheduler** — stop the deployment's normal qcluster
   processes (systemd/docker-compose scale) and the scheduler-enabling entry
   points. Web/API traffic may stay up: it only enqueues; nothing executes.
2. **Drain** — start exactly one drain worker with the scheduler disabled:

   ```bash
   DJANGO_SETTINGS_MODULE=core.settings.queue_drain python manage.py qcluster --run-once
   ```

   Wait for an empty ORM broker (`django_q.OrmQ` count 0) and for the drain
   worker to exit. Running-state proof: the parent/sentinel/pusher/monitor and
   worker processes must be gone — verify with the platform's process tooling
   (e.g. `systemctl status itambox-qcluster`, `ps`), not list emptiness alone.
3. **Snapshot** — one more database dump per `backup-restore.md` (the
   pre-cutover restore point).
4. **Strict preflight**:

   ```bash
   python manage.py verify_issue445_task_cutover --database default \
     --phase forward-preflight --strict
   ```

   Refuse to continue on any mismatch or undecodable surface.
5. **Migrate with workers off**:

   ```bash
   python manage.py migrate extras 0110_issue445_task_paths
   python manage.py migrate                     # apply remaining pending migrations
   ```

   The migration-aware `post_migrate` handler creates exactly one canonical
   daily alert schedule on this database.
6. **Strict post-migrate verification**:

   ```bash
   python manage.py verify_issue445_task_cutover --database default \
     --phase forward-postmigrate --strict
   python manage.py check
   python manage.py makemigrations --check --dry-run
   ```

7. **Isolated qcluster smoke** (fresh migrated database, empty unique broker,
   `sync=False`, `scheduler=False`):

   ```bash
   python manage.py qcluster --run-once
   ```

   It must start real child processes and exit without import, registry,
   migration, broker, or signature errors.
8. **Canary** — start one new-normal qcluster against the cutover code, watch
   one scheduled-report/alert/webhook round trip, then resume the full
   producer set. Old and new normal qclusters must never overlap.

## Reverse (rollback of a cutover that must be undone)

Refused by the historical-Task admin guard; a reverse requires the
restore-first procedure, never mutation of audit rows.

1. Stop all qclusters and producers (as step 1 above).
2. Drain per step 2 and take the post-cutover snapshot.
3. Restore the pre-cutover snapshot from step 3 (backup-restore.md restore
   procedure), then verify:

   ```bash
   python manage.py verify_issue445_task_cutover --database default \
     --phase reverse-postmigrate --strict
   python manage.py check
   ```

4. Resume producers from the restored state.

## Restore-first (when a reverse migration is undesirable)

1. Snapshot the cutover state (for the incident record; never delete it).
2. Restore the pre-cutover snapshot and verify per *Reverse* step 3.
3. Re-run pending non-queue migrations, then resume producers.
4. Historical `django_q.Task` rows remain byte-identical in every path;
   resubmission of predecessor paths is rejected all-or-nothing by the
   application-owned guard with code `task_resubmission.blocked_moved_path`.

## Verification ladder

| Gate | Command |
|---|---|
| Architecture | `python scripts/check_architecture.py` |
| Local imports | `python scripts/check_local_imports.py` |
| Flake8 baseline | `python scripts/check_flake8_baseline.py` |
| Exception policy | `python scripts/check_exception_policy.py` |
| Migration audit | `python scripts/migration_audit.py --check` |
| Contract policy | `python scripts/check_contract_policy.py` |
| Typing | `python scripts/check_typing_policy.py` |
| Migration lifecycle | `pytest extras/tests/test_issue445_task_path_migration.py` |
| Queue surfaces | `pytest extras/tests/test_issue445_queue_cutover.py` |
| OpenAPI | `make openapi-check` |
