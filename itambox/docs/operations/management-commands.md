# Management Commands

ITAMbox ships a set of Django management commands for maintenance, troubleshooting, and data operations. All commands are invoked from the repository root (or the directory containing `manage.py`):

```bash
python manage.py <command> [options]
```

In the Docker Compose stack, prefix with `docker compose run --rm app`:

```bash
docker compose run --rm app python manage.py <command> [options]
```

---

## Command Reference

### `compile_locales`

Compile `.po` translation catalogs to `.mo` binaries using a pure-Python `msgfmt` implementation — no GNU gettext required.

| | |
|---|---|
| **Usage** | `python manage.py compile_locales [LOCALE ...]` |
| **Production-safe** | Yes (read-only on `.po` files; writes `.mo` binaries into the locale tree) |
| **When to use** | After updating translation `.po` files, or after deploying a revision that changed translatable strings. Without compilation, Django serves fallback (English) strings. |

!!! note "Pure-Python, no gettext dependency"
    This command works on Windows hosts where GNU gettext is not installed. It replaces the old `itambox/compile_locale.py` standalone script.

**Options**

| Option | Purpose |
|---|---|
| `LOCALE` (positional, repeatable) | Locale codes to compile (e.g. `de`, `fr`). Omit to compile all discovered locales. |

**Examples**

```bash
# Compile all locales
python manage.py compile_locales

# Compile only German
python manage.py compile_locales de
```

---

### `export_datamodel`

Export the ITAMbox domain model and their direct relations as a Graphviz DOT graph. Useful for documentation, architecture reviews, and onboarding.

| | |
|---|---|
| **Usage** | `python manage.py export_datamodel [--apps ...] [--output PATH] [--hide-cross-cutting]` |
| **Production-safe** | Yes (read-only; only reads the Django model registry) |
| **When to use** | Generating data-model diagrams for documentation, reviewing schema changes, or understanding cross-app relationships. |

**Options**

| Option | Purpose |
|---|---|
| `--apps APP [APP ...]` | Limit the graph to one or more domain apps. Valid choices: `assets`, `compliance`, `core`, `extras`, `inventory`, `licenses`, `organization`, `procurement`, `software`, `subscriptions`, `users`. |
| `--output PATH` | Write DOT output to a file instead of stdout. Parent directories are created automatically. |
| `--hide-cross-cutting` | Omit ubiquitous `Tenant`, `User`, and `Tag` relations for a cleaner business-domain overview. |

**Examples**

```bash
# Full model graph to stdout
python manage.py export_datamodel

# Business-domain overview (no tenant/user/tag noise)
python manage.py export_datamodel --hide-cross-cutting

# Scope to a few apps, write to file
python manage.py export_datamodel --apps organization assets inventory --output docs/data-model.dot

# Render to SVG (requires Graphviz installed)
python manage.py export_datamodel --apps organization assets --output /tmp/model.dot
dot -Tsvg /tmp/model.dot -o docs/model.svg
```

---

### `integrity_report`

Run all tenant-integrity checks across every app and report violations. This is a **read-only** command — it never writes to the database.

| | |
|---|---|
| **Usage** | `python manage.py integrity_report [--json] [--proposals PATH] [--fail-on-findings]` |
| **Production-safe** | Yes (read-only) |
| **When to use** | Auditing multi-tenant data consistency, pre-migration validation, CI gating, or investigating cross-tenant data leaks. |

**Checks performed**

| Check | What it looks for |
|---|---|
| `null_tenant` | Operational rows with `tenant=NULL` |
| `stock_tenant_conflict` | Stock pools with conflicting item/location tenants |
| `cross_tenant_assignment` | Cross-tenant asset assignments |
| `location_site_tenant_mismatch` | Location/Site tenant mismatches |
| `po_tenant_mismatch` | Purchase orders vs destination location |
| `po_line_tenant_mismatch` | Purchase-order lines vs purchase order |
| `po_line_item_tenant_mismatch` | Purchase-order lines vs catalogue item |
| `license_seat_tenant_mismatch` | License seats vs assignment target |
| `custody_tenant_mismatch` | Custody receipts: asset vs holder |
| `rbac_grant_inconsistent` | RBAC grants: role owner vs principal tenant |
| `rbac_group_inconsistent` | User groups: ownership/membership consistency |

Each finding is classified (e.g. sharing-eligible, orphan, mismatch) so operators can decide whether to grant sharing access or fix the data.

**Options**

| Option | Purpose |
|---|---|
| `--json` | Emit the full report as JSON on stdout instead of human-readable text. |
| `--proposals PATH` | Write proposed `TenantResourceGrant` payloads (JSON) to a file for operator review. |
| `--fail-on-findings` | Exit with a non-zero status when any finding is reported — suitable for CI gates. |

**Examples**

```bash
# Readable report
python manage.py integrity_report

# JSON output for scripting
python manage.py integrity_report --json

# CI gate: fail if there are any findings
python manage.py integrity_report --fail-on-findings

# Full audit with proposed grants
python manage.py integrity_report --proposals /tmp/grants.json --fail-on-findings
```

---

### `list_failed_tasks`

List recently failed django-q2 background tasks with their function names, timestamps, attempt counts, and tracebacks.

| | |
|---|---|
| **Usage** | `python manage.py list_failed_tasks [--limit N]` |
| **Production-safe** | Yes (read-only) |
| **When to use** | Troubleshooting background job failures, investigating alerting gaps, or verifying task health after a worker outage. |

**Options**

| Option | Default | Purpose |
|---|---|---|
| `--limit N` | `20` | Maximum number of failed tasks to show, most recent first. |

**Examples**

```bash
# Show the 20 most recent failures
python manage.py list_failed_tasks

# Show only the 5 most recent
python manage.py list_failed_tasks --limit 5
```

!!! note "Successful tasks are not persisted"
    django-q2 is configured with `save_limit = -1`, so only **failed** tasks are written to the database. Successful runs are fire-and-forget. The `Failure` table (which this command reads) is pruned by `prune_changelog` according to `ITAMBOX_QTASK_FAILED_RETENTION_DAYS`.

---

### `purge_deleted`

Permanently hard-delete soft-deleted rows that are older than the specified retention period. This is the irreversible counterpart to the soft-delete mechanism.

| | |
|---|---|
| **Usage** | `python manage.py purge_deleted [--days N] [--dry-run]` |
| **Production-safe** | **With caution.** Destroys data irreversibly. Always run `--dry-run` first. |
| **When to use** | Reclaiming storage from long-deleted records, enforcing data-retention policies for soft-deleted rows, or preparing for a backup window by removing dead rows. |

The command iterates over every model registered with the `soft_delete` feature in the ITAMbox model registry. All deletes are attributed in the audit trail (`ObjectChange`) with a system-actor marker. The purge spans all tenants; there is no per-tenant scoping.

**Options**

| Option | Default | Purpose |
|---|---|---|
| `--days N` | `30` | Delete objects soft-deleted more than N days ago. |
| `--dry-run` | `false` | Show what would be purged without deleting anything. |

**Examples**

```bash
# See how many rows would be purged with the default 30-day cutoff
python manage.py purge_deleted --dry-run

# Purge rows deleted more than 90 days ago (after verifying with --dry-run)
python manage.py purge_deleted --days 90

# Aggressive 7-day retention with dry-run first
python manage.py purge_deleted --days 7 --dry-run
```

!!! danger "Irreversible — no undo"
    Hard-deleted rows cannot be recovered through the application. Ensure your backup strategy covers the retention window you need before running this command without `--dry-run`. The audit trail records each purge event, but the purged data itself is gone.

---

### `rotate_encryption_keys`

Re-encrypt every encrypted database field with the current primary Fernet key. Used when rotating `ITAMBOX_FIELD_ENCRYPTION_KEYS`.

| | |
|---|---|
| **Usage** | `python manage.py rotate_encryption_keys [--dry-run]` |
| **Production-safe** | **With caution.** Mutates encrypted data in-place. Back up the database first. |
| **When to use** | After appending a new primary key to `ITAMBOX_FIELD_ENCRYPTION_KEYS` and before removing the old key. Until rotation completes, the old key must remain in the keyring so existing ciphertexts can be decrypted. |

**Encrypted fields covered**

| Model | Field | What it stores |
|---|---|---|
| `licenses.License` | `product_key` | Software license keys |
| `core.EmailSettings` | `smtp_password` | Outbound SMTP credentials |
| `extras.WebhookEndpoint` | `secret` | Webhook signing secrets |

The command reads every row (including soft-deleted rows, across all tenants), decrypts each value with the full keyring, re-encrypts with the current primary key, and saves the new ciphertext via `update()` (bypassing model `save()` to avoid double-encryption).

**Options**

| Option | Purpose |
|---|---|
| `--dry-run` | Simulate the rotation — report what would change without writing to the database. |

**Examples**

```bash
# Preview what will be rotated
python manage.py rotate_encryption_keys --dry-run

# Execute the rotation
python manage.py rotate_encryption_keys
```

!!! danger "Back up before rotating"
    Key rotation rewrites every encrypted field in the database. If the process is interrupted or the new key is lost before the rotation completes, encrypted values may become unreadable. Take a full database backup immediately before running this command. Keep every retired key in the keyring until rotation completes successfully — the command needs the old key to decrypt existing rows.

!!! warning "Plaintext fallback adoption"
    If a field contains a value that is **not** `enc$`-prefixed (a plaintext fallback from before encryption was enabled), the command treats it as cleartext and encrypts it under the current key. This is intentional: it brings legacy plaintext values into the encryption regime. Review any such rows in the output to ensure they are expected.

---

### `run_jobs`

Process pending background jobs from the database job queue (`core.Job` model). This is distinct from the django-q2 task cluster — it operates on ITAMbox's own job queue.

| | |
|---|---|
| **Usage** | `python manage.py run_jobs` |
| **Production-safe** | Yes (runs jobs that are already queued and ready; no new work is scheduled) |
| **When to use** | Manually draining the job queue after a worker outage, testing job handlers, or running jobs synchronously when the background worker is unavailable. |

**Options**

This command takes no arguments or options.

**Examples**

```bash
# Process all pending jobs
python manage.py run_jobs
```

!!! note "Job handler dispatch"
    The command dispatches jobs to handler methods named `_run_<job_name>` on the command class. Job names are normalised by replacing `:` and spaces with underscores and lowercasing. Jobs without a matching handler are silently skipped.

---

### `sync_tenant_ldap`

Synchronise users from an LDAP directory into a specific tenant. Creates or updates local Django `User` records, establishes `Membership` entries, and grants a default "Member" role.

| | |
|---|---|
| **Usage** | `python manage.py sync_tenant_ldap --tenant SLUG` |
| **Production-safe** | Yes (creates and updates users; does not delete existing users) |
| **When to use** | Initial bulk import of LDAP users into a tenant, periodic synchronisation to pick up new directory entries, or testing LDAP configuration before enabling real-time authentication. |

**Prerequisites**

- `django-auth-ldap` must be installed (`pip install django-auth-ldap`)
- `ITAMBOX_TENANT_LDAP_CONFIGS` must contain an entry for the target tenant slug with at minimum `SERVER_URI`, `BIND_DN`, `BIND_PASSWORD`, and `USER_SEARCH_BASE`

**Options**

| Option | Required | Purpose |
|---|---|---|
| `--tenant SLUG` | **Yes** | Slug of the tenant to sync users for. |

**Examples**

```bash
# Sync LDAP users into the "acme-corp" tenant
python manage.py sync_tenant_ldap --tenant acme-corp
```

!!! note "Group filtering"
    If the LDAP configuration includes a `REQUIRE_GROUP` entry, only users whose `memberOf` attribute contains that group DN are synchronised. Omit `REQUIRE_GROUP` to sync all users matching the search filter.

!!! note "Role grants are time-limited for privileged roles"
    Users synchronised via LDAP receive a "Member" role with `SCOPE_OWN` by default. If the role is classified as privileged (requiring MFA), the grant carries a 24-hour lifetime. Manual role grants from the admin UI are never overwritten by the sync.

---

### `validate_role_permissions`

Scan every `Role.permissions` JSON field across all tenants and flag any permission codename that does not match a real `auth.Permission` in the database.

| | |
|---|---|
| **Usage** | `python manage.py validate_role_permissions` |
| **Production-safe** | Yes (read-only) |
| **When to use** | After a migration that renames or removes models, after cleaning up stale permissions, or as a periodic audit to catch typos in permission codenames. |

**Options**

This command takes no arguments or options. It exits with a non-zero status when stale codenames are found (suitable for CI gates).

**Examples**

```bash
# Audit all roles
python manage.py validate_role_permissions

# Use in CI
python manage.py validate_role_permissions || echo "Stale permissions found — review and fix"
```

!!! note "JSONField, no FK integrity"
    `Role.permissions` is a JSONField of `app_label.codename` strings. Unlike a many-to-many through table, there is no database-enforced foreign key to `auth.Permission`. A typo, a renamed model, or a removed permission silently persists as a dead entry that grants no actual access. This command is the safety net.

---

## Commands Documented Elsewhere

The following commands have dedicated documentation pages or are covered in detail in other sections:

| Command | Documented in |
|---|---|
| `import_snipeit` | [Migrate from Snipe-IT](../integration/migrate-from-snipe-it.md) |
| `sync_intune` | [Discovery & Sync](../integration/discovery-sync.md) |
| `prune_changelog` | [Data Retention](data-retention.md) |
| `seed_data` | [Installation](installation.md) — development/demo only; **never run against production** |

---

## Scheduled Commands

The following tasks run automatically via django-q2 `Schedule` entries registered at application startup (`post_migrate` signal). No manual cron configuration is required when using the standard `qcluster` deployment.

| Schedule | Function | Frequency | Registered by |
|---|---|---|---|
| **Daily Alert Rule Evaluation** | `core.tasks.evaluate_alert_rules_task` | Daily | `CoreConfig._register_alert_schedule` |
| **Daily Changelog & Operational-Data Retention Prune** | `core.tasks.prune_changelog_task` | Daily | `CoreConfig._register_prune_schedule` |
| **Daily Subscription Expiries and Reminders** | `subscriptions.tasks.check_subscription_expiries_and_reminders` | Daily | `SubscriptionsConfig._register_subscription_tasks` |

All schedule registrations are idempotent: they use `core.schedules.register_schedule`, which collapses repeated calls into a single `Schedule` row per function. It is safe to run `migrate` or restart the application multiple times — duplicate schedules are never created.

!!! note "Out-of-band cron fallback"
    If you run background jobs outside the django-q2 cluster (or want a safety net), every scheduled command can also be invoked directly from system cron. The underlying management commands are safe to run ad hoc. See [Data Retention](data-retention.md#scheduling) for a cron example.

### Schedule verification

To verify the schedules are registered, query the django-q2 `Schedule` table:

```bash
python manage.py shell -c "
from django_q.models import Schedule
for s in Schedule.objects.all():
    print(f'{s.name}: {s.func} (next: {s.next_run})')
"
```
