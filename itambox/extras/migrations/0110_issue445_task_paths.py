"""Deployment migration: cut over persisted django-q task paths (issue #445).

Rewrites ``Schedule.func`` from the twelve canonical predecessor task paths
to their domain-owner paths in the forward direction and back in reverse.
Only ``func`` changes; every other schedule field (name, hook, args, kwargs,
schedule type, minutes, repeats, next run, cron, cluster, intended-date
data), row multiplicity and ordering is preserved.
"""

from django.db import migrations

TASK_PATH_MAP = {
    "core.tasks.evaluate_alert_rules_task": "extras.tasks.alerts.evaluate_alert_rules_task",
    "core.tasks.run_alert_rule_now": "extras.tasks.alerts.run_alert_rule_now",
    "core.tasks.generate_scheduled_report_task": "extras.tasks.reports.generate_scheduled_report_task",
    "core.tasks.send_webhook_task": "extras.tasks.webhooks.send_webhook_task",
    "assets.tasks.notify_new_request_task": "assets.tasks.requests.notify_new_request_task",
    "core.tasks.bulk_checkin_task": "assets.tasks.checkin.bulk_checkin_task",
    "core.tasks.bulk_checkout_task": "assets.tasks.checkout.bulk_checkout_task",
    "core.tasks.calculate_depreciation": "assets.tasks.depreciation.calculate_depreciation",
    "core.tasks.bulk_dispose_task": "assets.tasks.disposal.bulk_dispose_task",
    "core.tasks.sync_tenant_intune": "assets.tasks.intune_sync.sync_tenant_intune",
    "core.tasks.labels.generate_label_batch_task": "assets.tasks.labels.generate_label_batch_task",
    "core.tasks.labels.generate_label_pdf_batch_task": (
        "assets.tasks.labels.generate_label_pdf_batch_task"
    ),
}
REVERSE_TASK_PATH_MAP = {new: old for old, new in TASK_PATH_MAP.items()}


def _rewrite_schedule_func(apps, schema_editor, mapping):
    Schedule = apps.get_model("django_q", "Schedule")
    db_alias = schema_editor.connection.alias
    for old, new in mapping.items():
        Schedule.objects.using(db_alias).filter(func=old).update(func=new)


def forward(apps, schema_editor):
    _rewrite_schedule_func(apps, schema_editor, TASK_PATH_MAP)


def reverse(apps, schema_editor):
    _rewrite_schedule_func(apps, schema_editor, REVERSE_TASK_PATH_MAP)


class Migration(migrations.Migration):
    dependencies = [
        ("extras", "0109_webhookdelivery"),
        ("django_q", "0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
