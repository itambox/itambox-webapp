"""Scheduled Reports Stable promotion: fire identity, delivery ledger, and registration reconciliation.

Adds the fields the frozen V1 scheduled-report contract needs and reconciles
the django-q registration rows that the pre-promotion ``update_or_create``
registration could duplicate:

* ``ScheduledReport.last_accepted_fire_at`` — the newest intended occurrence
  accepted for execution. The worker claims a fire before doing any work, so a
  broker redelivery (``timeout`` 600 s / ``retry`` 660 s) or a duplicated
  occurrence at or before the claimed time is a no-op instead of a second
  dispatch.
* ``ReportGenerationArchive.delivery_status`` / ``delivery_targets`` — the
  per-run delivery ledger that separates compile/archive outcomes from the
  per-channel dispatch outcomes (email aggregate plus one entry per enabled
  notification channel), and lets ``Retry delivery`` re-attempt only the
  targets whose outcome was a failure.
* ``ReportGenerationArchive.disclosure_text`` — the scope/truncation
  disclosure the delivered output carried, kept so a redelivery stays faithful.

The reconciliation is deliberately non-destructive and upgrade-only:

* every existing report schedule row gains the ``intended_fire_at`` kwarg so
  the fire-identity claim works from the first fire after the upgrade;
* duplicate registration rows for one schedule name (possible before the
  advisory-locked registration) are collapsed onto the row the schedule
  references (falling back to the oldest), with ``ScheduledReport`` rows that
  referenced a removed duplicate re-pointed, so the duplicates can no longer
  double-fire deliveries and the active scheduling state is never replaced by
  a stale duplicate;
* nothing is activated, no ``is_active`` flag is touched, no ``next_run`` is
  backfilled, and no delivery state is rewritten — dormant or paused schedules
  stay exactly as the operator left them.

The reverse clears the ``intended_fire_at`` kwarg again: a predecessor
codebase's task signature does not accept the injected keyword argument, so a
downgrade that left the kwarg in place would fail every report task it fires.
The duplicate collapse is forward-only (deleted rows are not resurrected).
"""

import logging

from django.db import migrations, models

logger = logging.getLogger(__name__)

REPORT_TASK_PATH = "extras.tasks.reports.generate_scheduled_report_task"
REPORT_SCHEDULE_NAME_PREFIX = "scheduled_report_"
FIRE_KWARG = "intended_fire_at"


def reconcile_report_schedule_rows(apps, schema_editor):
    """Backfill the fire-identity kwarg and collapse duplicate registrations."""
    Schedule = apps.get_model("django_q", "Schedule")
    ScheduledReport = apps.get_model("extras", "ScheduledReport")
    using = schema_editor.connection.alias

    rows = list(
        Schedule.objects.using(using)
        .filter(func=REPORT_TASK_PATH, name__startswith=REPORT_SCHEDULE_NAME_PREFIX)
        .order_by("id")
    )
    by_name = {}
    for row in rows:
        by_name.setdefault(row.name, []).append(row)
    for name, group in by_name.items():
        if len(group) < 2:
            continue
        group_ids = [row.pk for row in group]
        referenced_ids = set(
            ScheduledReport.objects.using(using)
            .filter(schedule_id__in=group_ids)
            .values_list("schedule_id", flat=True)
        )
        # Prefer the row the report actually references as the survivor: the
        # referenced row carries the live next_run/cadence state, and
        # collapsing a report onto an older duplicate could silently replace
        # the schedule the operator is running with a stale one.
        keeper = next((row for row in group if row.pk in referenced_ids), group[0])
        removed_ids = [row.pk for row in group if row.pk != keeper.pk]
        # Re-point only the reports whose referenced row is being removed; the
        # keeper keeps its reference (and its scheduling state) untouched.
        ScheduledReport.objects.using(using).filter(schedule_id__in=removed_ids).update(schedule_id=keeper.pk)
        Schedule.objects.using(using).filter(pk__in=removed_ids).delete()
        logger.warning(
            "Collapsed %d duplicate scheduled-report registration row(s) for %s onto %s",
            len(removed_ids),
            name,
            keeper.pk,
        )

    # Backfill the fire-identity kwarg on every row that fires the report task
    # (not only the name-prefixed ones), so redelivery is a no-op from the
    # first fire after the upgrade.
    Schedule.objects.using(using).filter(func=REPORT_TASK_PATH).exclude(intended_date_kwarg=FIRE_KWARG).update(
        intended_date_kwarg=FIRE_KWARG
    )


def clear_fire_kwarg(apps, schema_editor):
    """Remove the injected kwarg so a downgraded task signature stays callable."""
    Schedule = apps.get_model("django_q", "Schedule")
    using = schema_editor.connection.alias
    Schedule.objects.using(using).filter(func=REPORT_TASK_PATH, intended_date_kwarg=FIRE_KWARG).update(
        intended_date_kwarg=""
    )


class Migration(migrations.Migration):
    dependencies = [
        ("extras", "0123_pause_flag_suppressed_report_schedules"),
        ("django_q", "0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.AddField(
            model_name="scheduledreport",
            name="last_accepted_fire_at",
            field=models.DateTimeField(
                blank=True,
                editable=False,
                help_text=(
                    "Newest intended run time accepted for execution. A redelivered or duplicated occurrence "
                    "at or before this time is a no-op, so broker redelivery can never dispatch a run twice."
                ),
                null=True,
                verbose_name="Last Accepted Fire",
            ),
        ),
        migrations.AddField(
            model_name="reportgenerationarchive",
            name="delivery_status",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Outcome of the delivery fan-out for this run: blank (not dispatched or pre-upgrade row), "
                    "'none' (no targets configured), 'success', 'partial', or 'failed'."
                ),
                max_length=20,
                verbose_name="Delivery Status",
            ),
        ),
        migrations.AddField(
            model_name="reportgenerationarchive",
            name="delivery_targets",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    "Per-target delivery ledger of this run (email aggregate and one entry per notification "
                    "channel). Retry delivery re-attempts only targets recorded as failed."
                ),
                verbose_name="Delivery Targets",
            ),
        ),
        migrations.AddField(
            model_name="reportgenerationarchive",
            name="disclosure_text",
            field=models.TextField(
                blank=True,
                default="",
                help_text=(
                    "Scope/truncation disclosure carried by the delivered output, kept for faithful redelivery."
                ),
                verbose_name="Disclosure Text",
            ),
        ),
        migrations.RunPython(reconcile_report_schedule_rows, clear_fire_kwarg),
    ]
