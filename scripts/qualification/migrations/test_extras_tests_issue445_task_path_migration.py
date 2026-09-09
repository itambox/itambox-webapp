"""Migration rehearsal from itambox/extras/tests/test_issue445_task_path_migration.py — run explicitly:

    PYTHONPATH=itambox pytest scripts/qualification/migrations/
"""

'Migration rehearsal for the issue #445 django-q task-path cutover.\n\nProves the complete forward/reverse lifecycle of\n``extras.0110_issue445_task_paths`` against a real PostgreSQL database: the\npredecessor state, forward mapping with byte-equal non-func fields and row\nmultiplicity, reverse restoration of every predecessor path, one repeated\nforward, and teardown back to the graph leaves — plus, in a separate serial\ntest, the migration-aware post-migrate alert registration (exactly one\ncanonical daily alert schedule; none on the predecessor state or after\nreverse).\n\nBoth tests run the whole lifecycle in one serial test method so schema state\ncan never leak between TransactionTestCase methods.\n'

import pytest

from django.apps import apps as django_apps

from django.db import connection

from django.db.migrations.executor import MigrationExecutor

from django.db.models.signals import post_migrate

from core.tests.migration_harness import IsolatedMigrationTestCase, isolate_migration_tests

TASK_PATH_MAP = {'core.tasks.evaluate_alert_rules_task': 'extras.tasks.alerts.evaluate_alert_rules_task', 'core.tasks.run_alert_rule_now': 'extras.tasks.alerts.run_alert_rule_now', 'core.tasks.generate_scheduled_report_task': 'extras.tasks.reports.generate_scheduled_report_task', 'core.tasks.send_webhook_task': 'extras.tasks.webhooks.send_webhook_task', 'assets.tasks.notify_new_request_task': 'assets.tasks.requests.notify_new_request_task', 'core.tasks.bulk_checkin_task': 'assets.tasks.checkin.bulk_checkin_task', 'core.tasks.bulk_checkout_task': 'assets.tasks.checkout.bulk_checkout_task', 'core.tasks.calculate_depreciation': 'assets.tasks.depreciation.calculate_depreciation', 'core.tasks.bulk_dispose_task': 'assets.tasks.disposal.bulk_dispose_task', 'core.tasks.sync_tenant_intune': 'assets.tasks.intune_sync.sync_tenant_intune', 'core.tasks.labels.generate_label_batch_task': 'assets.tasks.labels.generate_label_batch_task', 'core.tasks.labels.generate_label_pdf_batch_task': 'assets.tasks.labels.generate_label_pdf_batch_task'}

MIGRATE_FROM = [('extras', '0109_webhookdelivery'), ('django_q', '0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more'), ('users', '0100_issue88_shard_62_users_relations')]

MIGRATE_TO = ('extras', '0110_issue445_task_paths')

ALERT_PATH = 'extras.tasks.alerts.evaluate_alert_rules_task'

NON_FUNC_FIELDS = ('name', 'hook', 'args', 'kwargs', 'schedule_type', 'minutes', 'repeats', 'next_run', 'cron', 'cluster', 'task', 'intended_date_kwarg')

@isolate_migration_tests
@pytest.mark.serial_only
class Issue445TaskPathMigrationTests(IsolatedMigrationTestCase):
    """Full forward/reverse/forward lifecycle of the persisted-path cutover."""
    reset_sequences = True

    def setUp(self):
        super().setUp()

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        return executor.migrate([target] if isinstance(target, tuple) else target)

    def _restore_leaves(self):
        Schedule = django_apps.get_model('django_q', 'Schedule')
        Schedule.objects.all().delete()
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def _seed_schedules(self, apps_state):
        Schedule = apps_state.get_model('django_q', 'Schedule')
        Schedule.objects.all().delete()
        rows = {}
        for old_path in TASK_PATH_MAP:
            for variant in (1, 2):
                schedule = Schedule.objects.create(func=old_path, hook=None, args='[]', kwargs='{}', schedule_type='D', minutes=0, repeats=-1, next_run=None, cron=None, cluster=None, task=None, name=f"Seeded {old_path.split('.')[-1]} variant {variant}")
                rows[schedule.pk] = schedule
        return rows

    def test_complete_forward_reverse_forward_lifecycle(self):
        try:
            old_apps = self._migrate(list(MIGRATE_FROM)).apps
            seeded = self._seed_schedules(old_apps)
            self.assertEqual(len(seeded), 24)
            new_apps = self._migrate([MIGRATE_TO]).apps
            NewSchedule = new_apps.get_model('django_q', 'Schedule')
            for old_path, new_path in TASK_PATH_MAP.items():
                expected = sum((1 for s in seeded.values() if s.func == old_path))
                self.assertEqual(NewSchedule.objects.filter(func=new_path).count(), expected, f'multiplicity drift on {new_path}')
            for pk, old_row in seeded.items():
                new_row = NewSchedule.objects.get(pk=pk)
                self.assertEqual(new_row.func, TASK_PATH_MAP[old_row.func])
                for field in NON_FUNC_FIELDS:
                    self.assertEqual(getattr(new_row, field), getattr(old_row, field), f'field {field} drifted on schedule {pk}')
            reversed_apps = self._migrate(list(MIGRATE_FROM)).apps
            ReversedSchedule = reversed_apps.get_model('django_q', 'Schedule')
            for old_path in TASK_PATH_MAP:
                self.assertGreaterEqual(ReversedSchedule.objects.filter(func=old_path).count(), 2)
            for pk, old_row in seeded.items():
                back = ReversedSchedule.objects.get(pk=pk)
                self.assertEqual(back.func, old_row.func)
                for field in NON_FUNC_FIELDS:
                    self.assertEqual(getattr(back, field), getattr(old_row, field), f'reverse drift {field} on {pk}')
            again_apps = self._migrate([MIGRATE_TO]).apps
            AgainSchedule = again_apps.get_model('django_q', 'Schedule')
            for old_path, new_path in TASK_PATH_MAP.items():
                expected = sum((1 for s in seeded.values() if s.func == old_path))
                self.assertEqual(AgainSchedule.objects.filter(func=new_path).count(), expected)
        finally:
            self._restore_leaves()

    def test_post_migrate_alert_registration_only_after_cutover_and_absent_on_reverse(self):
        try:
            old_apps = self._migrate(list(MIGRATE_FROM)).apps
            OldSchedule = old_apps.get_model('django_q', 'Schedule')
            extras_config = django_apps.get_app_config('extras')
            post_migrate.send(sender=extras_config, app_config=extras_config, verbosity=0, interactive=False, using=connection.alias)
            post_migrate.send(sender=extras_config, app_config=extras_config, verbosity=0, interactive=False, using=connection.alias)
            self.assertFalse(OldSchedule.objects.filter(func=ALERT_PATH).exists())
            new_apps = self._migrate([MIGRATE_TO]).apps
            NewSchedule = new_apps.get_model('django_q', 'Schedule')
            post_migrate.send(sender=extras_config, app_config=extras_config, verbosity=0, interactive=False, using=connection.alias)
            post_migrate.send(sender=extras_config, app_config=extras_config, verbosity=0, interactive=False, using=connection.alias)
            self.assertEqual(NewSchedule.objects.filter(func=ALERT_PATH).count(), 1)
            reversed_apps = self._migrate(list(MIGRATE_FROM)).apps
            ReversedSchedule = reversed_apps.get_model('django_q', 'Schedule')
            self.assertFalse(ReversedSchedule.objects.filter(func=ALERT_PATH).exists())
            post_migrate.send(sender=extras_config, app_config=extras_config, verbosity=0, interactive=False, using=connection.alias)
            self.assertFalse(ReversedSchedule.objects.filter(func=ALERT_PATH).exists())
        finally:
            self._restore_leaves()
