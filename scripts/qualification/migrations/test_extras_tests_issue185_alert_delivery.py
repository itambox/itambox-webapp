"Migration rehearsal from itambox/extras/tests/test_issue185_alert_delivery.py — run explicitly:\n\n    PYTHONPATH=itambox pytest scripts/qualification/migrations/\n"

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from core.tests.migration_harness import IsolatedMigrationTestCase, isolate_migration_tests

User = get_user_model()


@isolate_migration_tests
@pytest.mark.serial_only
class AlertDeliveryOutcomeMigrationTests(IsolatedMigrationTestCase):
    """Migration 0108 derives filterable outcomes from legacy payloads (N11/U4)."""

    reset_sequences = True
    migrate_from = ("extras", "0107_scheduledreportscopeauthorization_revocation")
    migrate_to = ("extras", "0108_alertlog_delivery_outcome")

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)

    def _migrate(self, target):
        self.executor = MigrationExecutor(connection)
        return self.executor.migrate([target])

    def tearDown(self):
        try:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
        finally:
            super().tearDown()

    def test_forward_derives_outcomes_and_reverse_preserves_rows(self):
        old_apps = self._migrate(self.migrate_from).apps
        Tenant = old_apps.get_model("organization", "Tenant")
        AlertRule = old_apps.get_model("extras", "AlertRule")
        AlertLog = old_apps.get_model("extras", "AlertLog")
        ContentType = old_apps.get_model("contenttypes", "ContentType")
        tenant = Tenant.objects.create(name="WP-13 Migration Tenant", slug="wp-13-migration-tenant")
        rule = AlertRule.objects.create(
            tenant_id=tenant.pk, name="WP-13 Migration Rule", alert_type="low_stock", threshold_value=1
        )
        rule_ct = ContentType.objects.get(app_label="extras", model="alertrule")
        cases = [
            ({"7": "ok"}, "delivered"),
            ({"7": "failed"}, "failed"),
            ({"7": "error: SMTP rejected"}, "failed"),
            ({"7": "retryable"}, "failed"),
            ({"__dispatch__": "pending"}, "pending"),
            ({"__dispatch__": "terminal"}, "failed"),
            ({"__no_channels__": "no channels attached to this rule"}, "none"),
            ({}, "none"),
            ({"7": {"disposition": "success", "operation": "in_app.deliver"}}, "delivered"),
            ({"7": {"disposition": "terminal", "error_class": "SMTPException"}}, "failed"),
        ]
        expected = {}
        for index, (payload, outcome) in enumerate(cases):
            log = AlertLog.objects.create(
                rule_id=rule.pk,
                subject=f"case-{index}",
                message="m",
                content_type_id=rule_ct.pk,
                object_id=index + 1,
                tenant_id=tenant.pk,
                status="active",
                delivery_status=payload,
            )
            expected[log.pk] = outcome
        new_apps = self._migrate(self.migrate_to).apps
        NewAlertLog = new_apps.get_model("extras", "AlertLog")
        for pk, outcome in expected.items():
            self.assertEqual(
                NewAlertLog.objects.get(pk=pk).delivery_outcome, outcome, f"payload case {pk} derived wrong outcome"
            )
        self._migrate(self.migrate_from)
        old_again = MigrationExecutor(connection).loader.project_state(self.migrate_from).apps
        OldAlertLog = old_again.get_model("extras", "AlertLog")
        self.assertEqual(OldAlertLog.objects.count(), len(cases))
        first = OldAlertLog.objects.order_by("pk").first()
        self.assertEqual(first.delivery_status, {"7": "ok"})
