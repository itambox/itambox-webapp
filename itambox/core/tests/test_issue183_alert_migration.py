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
        # pytest-django already created the database at the latest migration.
        # Keep the live schema stable and use the target historical app registry
        # to rehearse both data functions without desynchronising ORM state.
        self.apps = self.executor.loader.project_state([self.migrate_to]).apps

    def test_forward_and_reverse_preserve_unresolved_alert_rows(self):
        Tenant = self.apps.get_model("organization", "Tenant")
        AlertRule = self.apps.get_model("extras", "AlertRule")
        AlertLog = self.apps.get_model("extras", "AlertLog")
        ContentType = self.apps.get_model("contenttypes", "ContentType")

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

        # Re-run the forward data operation against rows created after schema setup.
        migration_module = __import__(
            "extras.migrations.0104_issue183_alert_tenant_reconciliation",
            fromlist=["reconcile_alert_tenants"],
        )
        migration_module.reconcile_alert_tenants(self.apps, None)
        resolved.refresh_from_db()
        unresolved.refresh_from_db()
        self.assertEqual(resolved.tenant_id, tenant.pk)
        self.assertEqual(resolved.tenant_resolution_status, "resolved")
        self.assertIsNone(unresolved.tenant_id)
        self.assertEqual(unresolved.tenant_resolution_status, "unresolved")

        migration_module.reverse_alert_tenant_reconciliation(self.apps, None)
        resolved.refresh_from_db()
        unresolved.refresh_from_db()
        self.assertEqual(resolved.tenant_id, tenant.pk)
        self.assertIsNone(unresolved.tenant_id)
        self.assertEqual(resolved.tenant_resolution_status, "resolved")
        self.assertEqual(unresolved.tenant_resolution_status, "unresolved")
        self.assertEqual(AlertLog.objects.count(), 2)
        self.assertEqual(resolved.subject, "Resolvable")
        self.assertEqual(resolved.message, "message")
        self.assertEqual(unresolved.subject, "Unresolved")
        self.assertEqual(unresolved.message, "message")
