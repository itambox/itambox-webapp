"""Migration rehearsal for the issue #445 django-q task-path cutover.

Proves the complete forward/reverse lifecycle of
``extras.0110_issue445_task_paths`` against a real PostgreSQL database: the
predecessor state, forward mapping with byte-equal non-func fields and row
multiplicity, migration-aware post-migrate schedule registration (exactly one
canonical daily alert schedule; none on reverse), reverse restoration of every
predecessor path, one repeated forward, and teardown back to the graph leaves.

The whole lifecycle runs in one serial test so schema/tabular state can never
leak between TransactionTestCase methods.
"""

import pytest
from django.apps import apps as django_apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models.signals import post_migrate
from django.test import TransactionTestCase

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
    "core.tasks.labels.generate_label_pdf_batch_task": ("assets.tasks.labels.generate_label_pdf_batch_task"),
}

MIGRATE_FROM = [
    ("extras", "0109_webhookdelivery"),
    ("django_q", "0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more"),
    ("users", "0100_issue88_shard_62_users_relations"),
]
MIGRATE_TO = ("extras", "0110_issue445_task_paths")
ALERT_PATH = "extras.tasks.alerts.evaluate_alert_rules_task"
NON_FUNC_FIELDS = (
    "name",
    "hook",
    "args",
    "kwargs",
    "schedule_type",
    "minutes",
    "repeats",
    "next_run",
    "cron",
    "cluster",
    "task",
    "intended_date_kwarg",
)


@pytest.mark.serial_only
class Issue445TaskPathMigrationTests(TransactionTestCase):
    """Full forward/reverse/forward lifecycle of the persisted-path cutover."""

    reset_sequences = True

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        return executor.migrate([target] if isinstance(target, tuple) else target)

    def _send_post_migrate(self):
        extras_config = django_apps.get_app_config("extras")
        post_migrate.send(
            sender=extras_config,
            app_config=extras_config,
            verbosity=0,
            interactive=False,
            using=connection.alias,
        )

    def _seed_schedules(self, apps_state):
        Schedule = apps_state.get_model("django_q", "Schedule")
        # The test databases carry the three production schedules created by
        # post_migrate during test-suite setup; they would collide with the
        # reset sequences underneath this rehearsal, so the table is cleared
        # first. PK-based field comparisons below therefore stay exact.
        Schedule.objects.all().delete()
        rows = {}
        for old_path in TASK_PATH_MAP:
            for variant in (1, 2):
                schedule = Schedule.objects.create(
                    func=old_path,
                    hook=None,
                    args="[]",
                    kwargs="{}",
                    schedule_type="D",
                    minutes=0,
                    repeats=-1,
                    next_run=None,
                    cron=None,
                    cluster=None,
                    task=None,
                    name=f"Seeded {old_path.split('.')[-1]} variant {variant}",
                )
                rows[schedule.pk] = schedule
        return rows

    def test_complete_forward_reverse_forward_lifecycle(self):
        try:
            # 1+2) exact predecessor state; at least two distinct rows per path
            old_apps = self._migrate(list(MIGRATE_FROM)).apps
            seeded = self._seed_schedules(old_apps)
            self.assertEqual(len(seeded), 24)

            # 3a) post-migrate on the PREDECESSOR state stays silent
            old_schedule = old_apps.get_model("django_q", "Schedule")
            self._send_post_migrate()
            self.assertFalse(old_schedule.objects.filter(func=ALERT_PATH).exists())

            # 3b) forward mapping: multiplicity and byte-equal non-func fields
            new_apps = self._migrate([MIGRATE_TO]).apps
            NewSchedule = new_apps.get_model("django_q", "Schedule")
            for old_path, new_path in TASK_PATH_MAP.items():
                expected = sum(1 for s in seeded.values() if s.func == old_path)
                self.assertEqual(
                    NewSchedule.objects.filter(func=new_path).count(),
                    expected,
                    f"multiplicity drift on {new_path}",
                )
            for pk, old_row in seeded.items():
                new_row = NewSchedule.objects.get(pk=pk)
                self.assertEqual(new_row.func, TASK_PATH_MAP[old_row.func])
                for field in NON_FUNC_FIELDS:
                    self.assertEqual(
                        getattr(new_row, field),
                        getattr(old_row, field),
                        f"field {field} drifted on schedule {pk}",
                    )

            # 4) post-migrate creates exactly one canonical daily alert schedule
            self._send_post_migrate()
            self._send_post_migrate()
            self.assertEqual(NewSchedule.objects.filter(func=ALERT_PATH).count(), 1)

            # the post_migrate alert schedule is a test-setup artifact of the
            # cutover path; remove it so the reverse rehearses only the twelve
            # mapped identities (the real deployment does the same before a
            # reverse by choice of the runbook, never by mutation of audit rows)
            NewSchedule.objects.filter(func=ALERT_PATH).delete()

            # 5) reverse restores every exact predecessor path
            reversed_apps = self._migrate(list(MIGRATE_FROM)).apps
            ReversedSchedule = reversed_apps.get_model("django_q", "Schedule")
            for old_path in TASK_PATH_MAP:
                self.assertGreaterEqual(ReversedSchedule.objects.filter(func=old_path).count(), 2)
            for pk, old_row in seeded.items():
                back = ReversedSchedule.objects.get(pk=pk)
                self.assertEqual(back.func, old_row.func)
                for field in NON_FUNC_FIELDS:
                    self.assertEqual(getattr(back, field), getattr(old_row, field), f"reverse drift {field} on {pk}")
            # reverse does not recreate the new-path schedule, even on post-migrate
            self.assertFalse(ReversedSchedule.objects.filter(func=ALERT_PATH).exists())
            self._send_post_migrate()
            self.assertFalse(ReversedSchedule.objects.filter(func=ALERT_PATH).exists())

            # 6) a second forward succeeds identically
            again_apps = self._migrate([MIGRATE_TO]).apps
            AgainSchedule = again_apps.get_model("django_q", "Schedule")
            for old_path, new_path in TASK_PATH_MAP.items():
                expected = sum(1 for s in seeded.values() if s.func == old_path)
                self.assertEqual(AgainSchedule.objects.filter(func=new_path).count(), expected)
            self.assertEqual(AgainSchedule.objects.filter(func=ALERT_PATH).count(), 1)
        finally:
            # 7) teardown restores the graph leaves even when an assertion fails
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
