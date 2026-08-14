"""Migration rehearsal for the issue #183 AlertLog tenant backfill."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


@pytest.mark.serial_only
class AlertTenantReconciliationMigrationTests(TransactionTestCase):
    reset_sequences = True

    migrate_from = ("extras", "0103_remove_reporttemplate_advanced_mode_and_more")
    migrate_to = ("extras", "0104_issue183_alert_tenant_reconciliation")

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)

    def _migrate(self, target):
        # Rebuild the executor after each direction: its loader caches the
        # applied-migration set at construction time.
        self.executor = MigrationExecutor(connection)
        return self.executor.migrate([target])

    def tearDown(self):
        # Restore the shared test database to the migration leaf state so later
        # tests never see a rehearsed (partially migrated) schema.
        try:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
        finally:
            super().tearDown()

    def test_forward_and_reverse_preserve_unresolved_alert_rows(self):
        try:
            old_apps = self._migrate(self.migrate_from).apps
            Tenant = old_apps.get_model("organization", "Tenant")
            AlertRule = old_apps.get_model("extras", "AlertRule")
            AlertLog = old_apps.get_model("extras", "AlertLog")
            ContentType = old_apps.get_model("contenttypes", "ContentType")

            tenant = Tenant.objects.create(name="Issue 183 Migration Tenant", slug="issue-183-migration-tenant")
            rule = AlertRule.objects.create(
                tenant=tenant,
                name="Issue 183 Migration Rule",
                alert_type="low_stock",
                threshold_value=1,
            )
            rule_ct = ContentType.objects.get(app_label="extras", model="alertrule")
            resolved = AlertLog.objects.create(
                rule=rule,
                subject="Resolvable",
                message="message",
                content_type_id=rule_ct.pk,
                object_id=rule.pk,
                tenant_id=None,
                status="active",
            )
            unresolved = AlertLog.objects.create(
                rule=rule,
                subject="Unresolved",
                message="message",
                content_type_id=rule_ct.pk,
                object_id=999999,
                tenant_id=None,
                status="active",
            )

            new_apps = self._migrate(self.migrate_to).apps
            NewAlertLog = new_apps.get_model("extras", "AlertLog")
            resolved = NewAlertLog.objects.get(pk=resolved.pk)
            unresolved = NewAlertLog.objects.get(pk=unresolved.pk)
            self.assertEqual(resolved.tenant_id, tenant.pk)
            self.assertEqual(resolved.tenant_resolution_status, "resolved")
            self.assertIsNone(unresolved.tenant_id)
            self.assertEqual(unresolved.tenant_resolution_status, "unresolved")

            reversed_apps = self._migrate(self.migrate_from).apps
            ReversedAlertLog = reversed_apps.get_model("extras", "AlertLog")
            resolved = ReversedAlertLog.objects.get(pk=resolved.pk)
            unresolved = ReversedAlertLog.objects.get(pk=unresolved.pk)
            self.assertEqual(resolved.tenant_id, tenant.pk)
            self.assertIsNone(unresolved.tenant_id)
            self.assertEqual(ReversedAlertLog.objects.count(), 2)
            self.assertEqual(resolved.subject, "Resolvable")
            self.assertEqual(resolved.message, "message")
            self.assertEqual(unresolved.subject, "Unresolved")
            self.assertEqual(unresolved.message, "message")
        finally:
            self._migrate(self.migrate_to)
