"""WP-13: additive delivery observability fields + row-level outcome derivation.

- Adds ``delivery_outcome`` (denormalized, filterable), ``delivery_attempts``,
  ``last_delivery_id``, ``last_delivery_error`` to ``AlertLog``.
- Derives ``delivery_outcome`` from existing ``delivery_status`` payloads
  (legacy string shapes AND the structured shape) so operators can filter
  alerts that fired but were not delivered without JSON lookups.
- Fully reversible: the added fields are dropped on reverse; the derivation is
  idempotent and does not mutate ``delivery_status`` itself.

Existing ``AlertLog`` rows and their ``delivery_status`` values are preserved
verbatim (additive contract of WP-13).
"""

from django.db import migrations, models


def _legacy_disposition(value):
    """Map a per-channel payload value (legacy string or structured dict) to a disposition."""
    if isinstance(value, dict):
        return value.get("disposition")
    if isinstance(value, str):
        if value == "ok":
            return "success"
        if value.startswith("error"):
            return "terminal"
        return value
    return None


def derive_delivery_outcome(payload):
    """Derive the filterable outcome from a ``delivery_status`` payload.

    Order matters: dispatch bookkeeping keys dominate, then the per-channel
    outcomes (any success => delivered; otherwise failed). Empty or
    channel-less payloads are ``none`` (no delivery planned).
    """
    if not payload:
        return "none"
    if payload.get("__dispatch__") == "pending":
        return "pending"
    if payload.get("__dispatch__") == "terminal":
        return "failed"
    if "__no_channels__" in payload:
        return "none"
    channel_values = [value for key, value in payload.items() if not key.startswith("__")]
    if not channel_values:
        return "none"
    dispositions = [
        disposition
        for disposition in (_legacy_disposition(value) for value in channel_values)
        if disposition is not None
    ]
    if any(disposition == "success" for disposition in dispositions):
        return "delivered"
    return "failed"


def derive_outcomes_forwards(apps, schema_editor):
    AlertLog = apps.get_model("extras", "AlertLog")
    for alert in AlertLog.objects.all().iterator():
        outcome = derive_delivery_outcome(alert.delivery_status or {})
        if outcome != alert.delivery_outcome:
            AlertLog.objects.filter(pk=alert.pk).update(delivery_outcome=outcome)


def derive_outcomes_backwards(apps, schema_editor):
    # The added fields are dropped on reverse; nothing to restore.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("extras", "0107_scheduledreportscopeauthorization_revocation"),
    ]

    operations = [
        migrations.AddField(
            model_name="alertlog",
            name="delivery_outcome",
            field=models.CharField(
                choices=[
                    ("none", "No delivery planned"),
                    ("pending", "Dispatch pending"),
                    ("delivered", "Delivered"),
                    ("failed", "Failed"),
                ],
                db_index=True,
                default="none",
                help_text="Denormalized single-attempt delivery outcome (none|pending|delivered|failed).",
                max_length=20,
                verbose_name="Delivery Outcome",
            ),
        ),
        migrations.AddField(
            model_name="alertlog",
            name="delivery_attempts",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Number of dispatch runs attempted for this alert (single-attempt policy: 1 per planned dispatch).",
                verbose_name="Delivery Attempts",
            ),
        ),
        migrations.AddField(
            model_name="alertlog",
            name="last_delivery_id",
            field=models.CharField(
                blank=True,
                help_text="Stable unique identifier of the most recent dispatch run; unchanged across retries of that run.",
                max_length=64,
                null=True,
                verbose_name="Last Delivery ID",
            ),
        ),
        migrations.AddField(
            model_name="alertlog",
            name="last_delivery_error",
            field=models.CharField(
                blank=True,
                help_text="Typed error class (or disposition) of the most recent failed channel delivery, if any.",
                max_length=255,
                null=True,
                verbose_name="Last Delivery Error",
            ),
        ),
        migrations.AlterField(
            model_name="alertlog",
            name="delivery_status",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Per-channel typed delivery outcome: {channel_pk: {disposition, operation, delivery_id, attempted_at, error_class?, message?}} plus dispatch bookkeeping keys (__dispatch__, __delivery_id__, __no_channels__). Legacy string values ('ok'|'failed'|'error: ...') remain readable.",
                verbose_name="Delivery Status",
            ),
        ),
        migrations.RunPython(derive_outcomes_forwards, derive_outcomes_backwards),
    ]
