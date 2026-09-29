"""Pause report schedules whose delivery the removed designer flag suppressed.

Issue #565 promotes the report designer to stable and removes
``ITAMBOX_FEATURE_REPORT_DESIGNER`` (and its one-release alias
``ITAMBOX_REPORT_DESIGNER_ENABLED``). While the flag was disabled -- the
historical default -- ``generate_scheduled_report_task`` skipped every
registered schedule with ``report.capability_inactive`` *without recording a
marker*: an existing django-q row kept firing and the report row kept its
``is_active=True``. With the guard gone those schedules would silently resume
outbound email/attachment delivery after the upgrade.

Stable/always-on may not introduce unsolicited external deliveries (#565), so
this one-shot migration pauses exactly that population for deployments that
ran the designer disabled, mirroring the operator-deactivation state
(``is_active=False``, no django-q row). Every row, its configuration, and its
``last_run``/``last_status`` history is preserved; re-enabling a schedule in
the UI resumes it deliberately. Deployments that ran the designer enabled keep
their django-q rows and keep delivering unchanged. The migration reads the
flag from the process environment because the setting no longer exists -- keep
the variable in place through the first upgraded start (see the upgrade
notes); application code ignores it afterwards.

Grandfathered templates kept delivering under the disabled flag and are
deliberately excluded. The operation is intentionally upgrade-only and refuses
to reverse: un-pausing by migration could re-arm deliveries nobody consented
to; rollback is restore-first or an explicit re-enable in the UI.
"""

import os

from django.db import migrations

CANONICAL_FLAG = "ITAMBOX_FEATURE_REPORT_DESIGNER"
LEGACY_FLAG_ALIAS = "ITAMBOX_REPORT_DESIGNER_ENABLED"
TRUTHY = frozenset({"1", "true", "yes", "on"})


def _designer_flag_was_enabled():
    """Whether the deployment ran the designer enabled at upgrade time.

    Only an explicit truthy value in either name counts as enabled; the flag
    defaulted to disabled, so an absent variable means the deployment ran with
    suppressed deliveries.
    """
    for name in (CANONICAL_FLAG, LEGACY_FLAG_ALIAS):
        value = os.environ.get(name)
        if value is not None and value.strip().lower() in TRUTHY:
            return True
    return False


def pause_flag_suppressed_schedules(apps, schema_editor):
    if _designer_flag_was_enabled():
        return
    ScheduledReport = apps.get_model("extras", "ScheduledReport")
    Schedule = apps.get_model("django_q", "Schedule")
    db_alias = schema_editor.connection.alias
    # Historical models expose plain (unscoped) managers; migrations also run
    # without an ambient tenant, so the queryset is the whole table by design.
    suppressed = list(
        ScheduledReport.objects.using(db_alias)
        .filter(is_active=True, schedule__isnull=False, report__legacy_designer_grandfathered=False)
        .values_list("pk", "schedule_id")
    )
    if not suppressed:
        return
    report_pks = [pk for pk, _ in suppressed]
    schedule_pks = [schedule_pk for _, schedule_pk in suppressed]
    ScheduledReport.objects.using(db_alias).filter(pk__in=report_pks).update(is_active=False, schedule=None)
    Schedule.objects.using(db_alias).filter(pk__in=schedule_pks).delete()


def reverse(apps, schema_editor):
    raise RuntimeError("issue565.report_schedule_transition.reverse_refused")


class Migration(migrations.Migration):
    dependencies = [
        ("extras", "0122_journalentry_tenant_group"),
        ("django_q", "0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [migrations.RunPython(pause_flag_suppressed_schedules, reverse)]
