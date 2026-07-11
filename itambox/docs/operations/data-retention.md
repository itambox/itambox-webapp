# Data Retention

ITAMbox does not keep operational/audit data forever by default. Four data classes are pruned on an age-based schedule by the `prune_changelog` management command:

| Data class | Model | Timestamp field | Setting | Default |
|---|---|---|---|---|
| Object changelog | `core.ObjectChange` | `time` | `ITAMBOX_CHANGELOG_RETENTION_DAYS` | 365 days |
| Alert log | `extras.AlertLog` | `created_at` | `ITAMBOX_ALERTLOG_RETENTION_DAYS` | 180 days |
| Notifications | `core.Notification` | `created_at` | `ITAMBOX_NOTIFICATION_RETENTION_DAYS` | 90 days |
| Failed background tasks | django-q2 `Failure` | `stopped` | `ITAMBOX_QTASK_FAILED_RETENTION_DAYS` | 90 days |

For every setting above, **`0` means unlimited — that data class is never pruned by age.**

!!! danger "Successful background tasks are never persisted"
    `Q_CLUSTER['save_limit'] = -1` (see `core/settings/base.py`) means django-q2 only ever persists **failed** tasks; successful runs are fire-and-forget and never written to the database at all. There is nothing to prune there — only the `Failure` table (failed tasks) accumulates, and that's what `ITAMBOX_QTASK_FAILED_RETENTION_DAYS` controls.

## Per-tenant changelog override (legal hold)

The object changelog additionally supports a per-tenant override: `Tenant.changelog_retention_days`.

- **Blank/null** (default) — the tenant follows the global `ITAMBOX_CHANGELOG_RETENTION_DAYS` setting.
- **A positive integer** — overrides the global window for this tenant only (shorter or longer).
- **`0`** — legal hold. This tenant's changelog is *never* pruned, no matter what the global setting says.

Global (tenant-less) changelog rows always follow the global setting; they have no per-row override.

## The `prune_changelog` command

```bash
python manage.py prune_changelog [options]
```

| Option | Purpose |
|---|---|
| `--changelog-days N` | Override `ITAMBOX_CHANGELOG_RETENTION_DAYS` for this run (`0` = unlimited). |
| `--alertlog-days N` | Override `ITAMBOX_ALERTLOG_RETENTION_DAYS` for this run. |
| `--notification-days N` | Override `ITAMBOX_NOTIFICATION_RETENTION_DAYS` for this run. |
| `--qtask-days N` | Override `ITAMBOX_QTASK_FAILED_RETENTION_DAYS` for this run. |
| `--tenant SLUG` | Restrict **changelog** pruning to one tenant (its `changelog_retention_days` override applies). Global rows and other tenants are left untouched. Has no effect on the other three classes. |
| `--classes a,b,c` | Comma-separated subset of `changelog,alertlog,notification,qtask` to prune (default: all four). |
| `--batch-size N` | Rows deleted per batch (default `10000`). Deletes are pk-chunked so a multi-million-row backlog doesn't hold one giant transaction/lock. |
| `--dry-run` | Report counts only; nothing is deleted or archived. |
| `--archive-dir DIR` | Stream pruned rows to JSONL (one file per class per run: `<class>_<timestamp>.jsonl`) before deleting them. |

### Examples

```bash
# See what a real run would delete, without deleting anything
python manage.py prune_changelog --dry-run

# Prune everything using the configured settings
python manage.py prune_changelog

# Archive before deleting (compliance-friendly)
python manage.py prune_changelog --archive-dir /var/backups/itambox/retention

# Only prune one tenant's changelog, with a tighter window than the global default
python manage.py prune_changelog --classes changelog --tenant acme-corp --changelog-days 90
```

!!! warning "Archiving is not transactional across batches"
    `--archive-dir` writes each batch to its JSONL file, flushes, and only then deletes that batch — as two separate operations, not one transaction. A process crash between the two can leave a batch archived-but-not-yet-deleted (harmless: the next run deletes it, at worst duplicating that batch across two archive files) or, far more rarely, deleted with an incomplete archive write if the process dies mid-write. Treat the archive directory as a best-effort export, not a guaranteed atomic backup — verify file integrity if you rely on it for compliance retention.

## Scheduling

`prune_changelog` runs automatically once a day: `core.tasks.prune_changelog_task` is registered as a django-q2 `Schedule` by `CoreConfig._register_prune_schedule` (`core/apps.py`), following the same idempotent `core.schedules.register_schedule` pattern used for alert-rule evaluation and subscription-expiry checks — no manual cron setup is required in the standard `qcluster` deployment.

If you run background jobs outside of `qcluster` (or want an out-of-band safety net), the command is also safe to invoke directly from system cron, e.g.:

```cron
# Run the prune once a day at 03:15, in addition to the built-in schedule
15 3 * * * cd /app/itambox && python manage.py prune_changelog >> /var/log/itambox/prune_changelog.log 2>&1
```

Running it twice in the same day is harmless — the second run simply finds nothing left to prune for rows already deleted by the first.
