"""Scheduled Reports Stable promotion: bind Retry delivery to the newest run's archive.

Adds ``ScheduledReport.last_run_archive`` — the generation archive retained by
the newest completed run. ``Retry delivery`` re-sends exactly the archive this
reference names, so a failed run can never be recovered by re-sending an older
report (for example when ``save_to_archive`` was turned off after an earlier
archived failure, or when the newest run retained no archive at all). The
reference is not backfilled: rows that predate the promotion keep a null
binding, so their retry action stays unavailable until the next completed run
binds a fresh archive — an operator recovers old failures through ``Run now``
instead of a stale redelivery. No schedule, approval, registration, or
delivery state is rewritten in either direction.

The reverse drops the column; no data migration is needed in either direction.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("extras", "0125_scheduled_report_fire_records_retry_hardening"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.AddField(
            model_name="scheduledreport",
            name="last_run_archive",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                help_text=(
                    "Output archive retained by the newest completed run. Retry delivery re-sends exactly "
                    "this archive; a run that retained none clears the reference, so an older archived "
                    "report is never redelivered in its place."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="extras.reportgenerationarchive",
                verbose_name="Last Run Archive",
            ),
        ),
    ]
