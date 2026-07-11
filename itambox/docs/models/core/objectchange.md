# Object Changelog

An **Object Change** record represents an immutable, comprehensive system audit trail capturing every creation, update, soft-delete, or recovery action executed on objects across the entire application.

## Attributes

| Field | Description | Type | Required |
| --- | --- | --- | --- |
| **Action** | Type of transaction: `Created`, `Modified`, `Deleted`. | Choice | Yes |
| **Changed Object** | Polymorphic pointer to the specific object database row using a Generic Foreign Key. | GFK | Yes |
| **Changed Object Id** | The changed object id of the object change. | Integer | Yes |
| **Changed Object Type** | The changed object type of the object change. | Foreign Key | Yes |
| **Object Repr** | The object repr of the object change. | String | Yes |
| **Object Type Repr** | The object type repr of the object change. | String | No |
| **Postchange Data** | The postchange data of the object change. | JSON | No |
| **Prechange Data** | The prechange data of the object change. | JSON | No |
| **Related Object** | The related object of the object change. | GenericForeignKey | Yes |
| **Related Object Id** | The related object id of the object change. | Integer | No |
| **Related Object Type** | The related object type of the object change. | Foreign Key | No |
| **Request ID** | A UUID tying multiple model changes back to a single HTTP request context. | UUID | Yes |
| **Tenant** | The tenant of the object change. | Foreign Key | No |
| **Time** | Precise timestamp of the transaction commit. | DateTime | Yes |
| **User** | The Django User who executed the change. | Foreign Key | No |
| **User Name** | Flat username backup (useful if the User account is later deleted). | String | Yes |

## Audit Integrity
ITAMbox generates changelogs asynchronously or at the database transaction layer using model lifecycle signals. Every detail view includes a "Changelog" tab displaying a clean diff table detailing exactly what fields changed, who did it, and when.

## Retention

Changelog rows are not kept forever by default. A daily job (see below) prunes rows against `ITAMBOX_CHANGELOG_RETENTION_DAYS` (default **365**; **0 = unlimited**, i.e. never pruned).

Tenants can override this globally-set window with `Tenant.changelog_retention_days`:

- **Blank/null** (default) — use the global `ITAMBOX_CHANGELOG_RETENTION_DAYS` setting.
- **A positive integer** — this tenant's changelog is pruned after that many days instead of the global default.
- **`0`** — legal hold. This tenant's changelog is never pruned, regardless of the global setting.

Rows with `tenant=None` (system/global changes) are pruned against the global setting only; they are left alone by any tenant-scoped run (`--tenant`).

### Pruning rows

Use the `prune_changelog` management command (it also prunes `AlertLog`, `Notification`, and failed django-q2 tasks in the same run — see [Data Retention](../../operations/data-retention.md) for the full picture):

```bash
# Prune only the changelog, using the configured/global retention window
python manage.py prune_changelog --classes changelog

# Preview what would be deleted without deleting anything
python manage.py prune_changelog --classes changelog --dry-run

# One-off override of the retention window for this run
python manage.py prune_changelog --classes changelog --changelog-days 180

# Restrict to a single tenant (its Tenant.changelog_retention_days override wins;
# global tenant=None rows and every other tenant are left untouched)
python manage.py prune_changelog --classes changelog --tenant acme-corp

# Archive pruned rows to JSONL before deleting them
python manage.py prune_changelog --classes changelog --archive-dir /var/backups/itambox/changelog
```

This command is scheduled to run daily and automatically (`core.tasks.prune_changelog_task`, registered as a django-q2 `Schedule` by `CoreConfig` in `core/apps.py`) — no manual cron setup is required in the standard deployment.
