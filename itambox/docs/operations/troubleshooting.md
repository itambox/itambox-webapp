# Troubleshooting

Ordered by the most common failure classes. Deeper procedures live in
[Backup & Restore](backup-restore.md), [Upgrades](upgrades.md), the
[Recovery qualification drill](recovery-drill.md), and the
[Management Commands](management-commands.md) reference.

## 1. Is the application healthy?

```bash
curl -I https://itam.example.com/health/
```

A reachable health endpoint answers `200`. If the host or port is wrong, check
`ITAMBOX_ALLOWED_HOSTS` (unrecognized hosts receive HTTP 400) and the reverse
proxy configuration (see [Installation](installation.md), section *Terminate
TLS at a reverse proxy*).

If the application does not start at all, the container logs are the first
evidence:

```bash
docker compose logs --tail=200 app worker
```

Startup-blocking conditions are loud: production refuses the insecure
`ITAMBOX_SECRET_KEY` fallback, SQLite is rejected at settings load, and
malformed `ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS` JSON aborts startup.
Fix those in `.env`, then restart.

## 2. Background workers and scheduled jobs

Scheduled work (retention pruning, alert-rule evaluation, subscription
expiries, resource-grant expiry, scheduled-report delivery) is executed by the
django-q2 worker (`qcluster`). **A stopped worker silently skips runs** — no
error is raised anywhere.

1. Check that the worker process is running: `docker compose ps` (service
   `worker`).
2. Verify the registered schedules:
   ```bash
   python manage.py shell -c "
   from django_q.models import Schedule
   for s in Schedule.objects.all():
       print(f'{s.name}: {s.func} (next: {s.next_run})')
   "
   ```
3. Inspect recent failures:
   ```bash
   python manage.py list_failed_tasks --limit 20
   ```
   The `Failure` table is pruned by `prune_changelog` per
   `ITAMBOX_QTASK_FAILED_RETENTION_DAYS` (see [Data Retention](data-retention.md)).

If a worker was down, jobs that should have run are not replayed — run the
underlying management command ad hoc if the effect matters (for example
`python manage.py prune_changelog`).

## 3. A capability is "off" or a Beta/Experimental badge is missing

Capability state is declared per capability, not per module:

```bash
python manage.py capabilities
```

- **Inactive, opt-in capability** — check the activation source: the
  operator flag (`ITAMBOX_FEATURE_REPORT_DESIGNER`, `ITAMBOX_PLUGINS`,
  `ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS`) or the application object
  (enabled `EventRule`, active `AlertRule`, SCIM token). See
  [Capability Maturity](capability-maturity.md).
- **`error` state** — the capability's probe could not run (for example the
  database is unreachable); the exception *type* is shown, never the message.
- **Plugin missing or failed** — see the plugin diagnostics banner in the UI
  and `python manage.py plugins`; a failed plugin is disabled without blocking
  Stable core ([plugin runbook](plugin-runbook.md)).

## 4. Data inconsistencies or suspected cross-tenant issues

```bash
python manage.py integrity_report
```

reports tenant-integrity violations (null tenants, stock/tenant conflicts,
cross-tenant assignments, RBAC inconsistencies) read-only. Use `--json` for
scripting and `--fail-on-findings` to gate a pipeline. Individual rows can be
proposed for repair as `TenantResourceGrant` payloads via `--proposals`.

## 5. Reports, webhooks, and notifications

- **Scheduled report did not arrive** — confirm the designer flag is enabled
  (`ITAMBOX_FEATURE_REPORT_DESIGNER=True`), the worker is running, the
  schedule row exists, and SMTP works (see below). A stopped worker is the
  most common cause.
- **Webhook not delivered** — deliveries are durable and recorded: open the
  event's delivery history (`WebhookDelivery`) for per-attempt status and
  outcomes, and use manual redelivery once the endpoint is fixed. Also check
  the `EventRule.enabled` flag, the `WebhookEndpoint` URL/secret, and the
  worker.
- **No alert emails** — alert-rule evaluation is daily, not continuous, and
  channel delivery failures are logged, not retried. Check `ITAMBOX_EMAIL_*`
  and the logs for the channel delivery attempt.
- **Email generally broken** — outbound SMTP is bounded by
  `ITAMBOX_EMAIL_TIMEOUT` (default 10 s). Verify host/port/TLS settings, then
  send a test reset/invite email. Raise `ITAMBOX_LOG_LEVEL` to `DEBUG` to
  inspect mail logging during the test, then reset it.

## 6. Rate limiting or client-IP confusion

- 429s for legitimate users behind a proxy — enable
  `ITAMBOX_RATELIMIT_USE_X_FORWARDED_FOR=True` **only** when every request
  goes through your trusted proxy, and set `ITAMBOX_RATELIMIT_NUM_PROXIES` to
  the hop count.
- 429s that should not exist in a direct deployment — keep
  `ITAMBOX_RATELIMIT_USE_X_FORWARDED_FOR=False`; a spoofed `X-Forwarded-For`
  would otherwise bypass per-IP limits.
- Limits resetting on every restart — the `locmem` cache is per-process; point
  `RATELIMIT_CACHE` at the shared Redis/Valkey backend. See
  [Deployment Security](../security/deployment-security.md).

## 7. After an upgrade or restore

- Everything above applies; additionally verify the applied migration identity
  and the schema against evidence from before the change:
  `python manage.py capture_schema_evidence` and
  `python manage.py capture_recovery_evidence` are the drill tools for exactly
  this ([Recovery qualification drill](recovery-drill.md)).
- If a restored database contains encrypted fields that no longer decrypt, the
  keyring used to encrypt is missing — restore the matching
  `ITAMBOX_FIELD_ENCRYPTION_KEYS` set ([Backup & Restore](backup-restore.md),
  [Deployment Security](../security/deployment-security.md)).
- Rollback is a redeploy of the previous reviewed revision plus database
  restore — see [Upgrades](upgrades.md).

## 8. Still stuck?

- Raise `ITAMBOX_LOG_LEVEL` to `DEBUG`, reproduce, and collect
  `docker compose logs`; reset to `INFO`/`WARNING` afterwards.
- Cross-check the data model page for the affected object (context-sensitive
  help in the UI) and the [management commands](management-commands.md)
  reference.
- Ask in [GitHub Discussions](https://github.com/itambox/itambox-webapp/discussions)
  with sanitized logs — never paste secrets, tokens, or real hostnames.
