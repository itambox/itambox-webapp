import importlib

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.graph import MigrationGraph
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase


class ReportDesignerMigrationTests(TransactionTestCase):
    reset_sequences = True
    migrate_from = ("extras", "0102_alter_event_action")
    migrate_to = ("extras", "0105_reporttemplate_advanced_mode_and_more")

    def tearDown(self):
        # Restore the shared test database to the migration leaf state so later
        # tests never see a rehearsed (partially migrated) schema.
        try:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
        finally:
            super().tearDown()

    def _historical_executor(self):
        # Keep this test focused on extras.0105; never reverse unrelated irreversible
        # Asset-Type cutovers just to reach the historical report state.
        executor = MigrationExecutor(connection)
        loader = executor.loader
        allowed = set(loader.graph.forwards_plan(self.migrate_to))
        graph = MigrationGraph()
        for key in allowed:
            graph.add_node(key, loader.disk_migrations[key])
        for key in allowed:
            migration = loader.disk_migrations[key]
            for dependency in migration.dependencies:
                if dependency in allowed:
                    graph.add_dependency(migration, key, dependency)
        loader.graph = graph
        return executor

    def setUp(self):
        super().setUp()
        MigrationRecorder(connection).record_unapplied("extras", "0113_upgrade_legacy_webhook_retry_schedules")
        self.executor = self._historical_executor()
        self.executor.migrate([self.migrate_from])
        old_apps = self.executor.loader.project_state([self.migrate_from]).apps
        ReportTemplate = old_apps.get_model("extras", "ReportTemplate")
        ScheduledReport = old_apps.get_model("extras", "ScheduledReport")
        Tenant = old_apps.get_model("organization", "Tenant")
        self.tenant = Tenant.objects.create(name="Migration Tenant", slug="migration-tenant")

        self.live_content = ReportTemplate.objects.create(
            name="live-content", report_type="asset_summary", tenant=self.tenant
        )
        self.live_empty = ReportTemplate.objects.create(
            name="live-empty", report_type="asset_summary", tenant=self.tenant
        )
        self.inactive_content = ReportTemplate.objects.create(
            name="inactive-content", report_type="asset_summary", tenant=self.tenant
        )
        self.unscheduled_content = ReportTemplate.objects.create(
            name="unscheduled-content", report_type="asset_summary", tenant=self.tenant
        )
        self.advanced_empty = ReportTemplate.objects.create(
            name="advanced-empty", report_type="asset_summary", tenant=self.tenant
        )
        ScheduledReport.objects.create(name="live", report=self.live_content, tenant=self.tenant, is_active=True)
        ScheduledReport.objects.create(name="live-empty", report=self.live_empty, tenant=self.tenant, is_active=True)
        ScheduledReport.objects.create(
            name="inactive", report=self.inactive_content, tenant=self.tenant, is_active=False
        )

        # Simulate values that existed before 0103 and remain recoverable in audit history.
        Change = old_apps.get_model("core", "ObjectChange")
        ContentType = old_apps.get_model("contenttypes", "ContentType")
        ct = ContentType.objects.get(app_label="extras", model="reporttemplate")
        Change.objects.create(
            tenant=self.tenant,
            user_name="migration",
            request_id="00000000-0000-0000-0000-000000000001",
            action="update",
            changed_object_type=ct,
            changed_object_id=self.live_content.pk,
            object_repr="live-content",
            postchange_data={"advanced_mode": True, "template_content": "<p>legacy</p>"},
        )

        connection.commit()
        connection.close()
        self.executor = self._historical_executor()
        self.executor.migrate([self.migrate_to])

    def test_only_live_non_empty_template_is_grandfathered(self):
        apps = self.executor.loader.project_state([self.migrate_to]).apps
        ReportTemplate = apps.get_model("extras", "ReportTemplate")
        rows = {row.name: row for row in ReportTemplate.objects.all()}
        self.assertTrue(rows["live-content"].legacy_designer_grandfathered)
        self.assertEqual(rows["live-content"].template_content, "<p>legacy</p>")
        for name in ("live-empty", "inactive-content", "unscheduled-content", "advanced-empty"):
            self.assertFalse(rows[name].legacy_designer_grandfathered)

    def test_upgrade_report_names_out_of_bound_custom_html_templates(self):
        connection.commit()
        connection.close()
        self.executor = self._historical_executor()
        self.executor.migrate([self.migrate_from])
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE extras_reporttemplate SET template_content = %s WHERE id = %s",
                ["<p>unscheduled legacy</p>", self.unscheduled_content.pk],
            )

        connection.commit()
        connection.close()
        self.executor = self._historical_executor()
        with self.assertLogs("extras.migrations.0105_reporttemplate_advanced_mode_and_more", level="WARNING") as logs:
            self.executor.migrate([self.migrate_to])

        report = "\n".join(logs.output)
        self.assertIn("unscheduled-content", report)
        self.assertIn(str(self.unscheduled_content.pk), report)
        self.assertNotIn("live-content", report)

    def test_forward_reverse_forward_is_idempotent_and_preserves_schema_and_content(self):
        migration = importlib.import_module("extras.migrations.0105_reporttemplate_advanced_mode_and_more")
        report_model = self.executor.loader.project_state([self.migrate_to]).apps.get_model("extras", "ReportTemplate")
        self.assertEqual(
            {"advanced_mode", "template_content", "legacy_designer_grandfathered"},
            {
                field.name
                for field in report_model._meta.local_fields
                if field.name in {"advanced_mode", "template_content", "legacy_designer_grandfathered"}
            },
        )

        # The forward data operation is idempotent and restores the values that
        # were serialized before 0103 removed them from historical ORM state.
        migration.recover_and_stamp_report_designer(
            self.executor.loader.project_state([self.migrate_to]).apps,
            type("SchemaEditor", (), {"connection": connection})(),
        )
        row = report_model.objects.get(name="live-content")
        original = (row.advanced_mode, row.template_content, row.legacy_designer_grandfathered)
        self.assertEqual(original, (True, "<p>legacy</p>", True))

        connection.commit()
        connection.close()
        self.executor = self._historical_executor()
        self.executor.migrate([self.migrate_from])
        connection.close()
        reversed_apps = self.executor.loader.project_state([self.migrate_from]).apps
        reversed_report = reversed_apps.get_model("extras", "ReportTemplate")
        reversed_row = reversed_report.objects.get(name="live-content")
        self.assertEqual((reversed_row.advanced_mode, reversed_row.template_content), (True, "<p>legacy</p>"))
        self.assertEqual(
            {field.name for field in reversed_report._meta.local_fields}
            & {"advanced_mode", "template_content", "legacy_designer_grandfathered"},
            {"advanced_mode", "template_content"},
        )
        with connection.cursor() as cursor:
            columns = {
                column.name
                for column in connection.introspection.get_table_description(cursor, reversed_report._meta.db_table)
            }
            quote = connection.ops.quote_name
            cursor.execute(
                f"SELECT {quote('legacy_designer_grandfathered')} "
                f"FROM {quote(reversed_report._meta.db_table)} WHERE {quote('id')} = %s",
                [reversed_row.pk],
            )
            marker_after_reverse = cursor.fetchone()[0]
        self.assertTrue({"advanced_mode", "template_content", "legacy_designer_grandfathered"} <= columns)
        self.assertFalse(marker_after_reverse)
        self.assertTrue(reversed_report.objects.filter(name="live-content", template_content="<p>legacy</p>").exists())

        # Re-applying the migration must not duplicate columns, rewrite content,
        # or broaden grandfathering beyond the bounded live-schedule set.
        connection.close()
        self.executor = self._historical_executor()
        self.executor.migrate([self.migrate_to])
        forward_apps = self.executor.loader.project_state([self.migrate_to]).apps
        forward_row = forward_apps.get_model("extras", "ReportTemplate").objects.get(name="live-content")
        self.assertEqual(
            (forward_row.advanced_mode, forward_row.template_content, forward_row.legacy_designer_grandfathered),
            original,
        )
        for name in ("live-empty", "inactive-content", "unscheduled-content", "advanced-empty"):
            self.assertFalse(
                forward_apps.get_model("extras", "ReportTemplate").objects.get(name=name).legacy_designer_grandfathered
            )
