import importlib
import os
from contextlib import contextmanager

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.graph import MigrationGraph
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase
from django.utils import timezone


def _ensure_retired_report_designer_columns():
    """Reconcile the columns the 0127 retirement removes at the migration head.

    The 0105 and 0123 rehearsals below stage their scenarios on the schema of
    their era, which still carries ``advanced_mode`` and
    ``legacy_designer_grandfathered`` physically. The retirement migration
    removes both columns at the head, so restore them explicitly (idempotent,
    mirroring 0105's own persistent-field reconciliation) before rehearsing.
    """
    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE extras_reporttemplate ADD COLUMN IF NOT EXISTS advanced_mode boolean DEFAULT FALSE")
        cursor.execute("UPDATE extras_reporttemplate SET advanced_mode = FALSE WHERE advanced_mode IS NULL")
        cursor.execute("ALTER TABLE extras_reporttemplate ALTER COLUMN advanced_mode SET DEFAULT FALSE")
        cursor.execute("ALTER TABLE extras_reporttemplate ALTER COLUMN advanced_mode SET NOT NULL")
        cursor.execute(
            "ALTER TABLE extras_reporttemplate ADD COLUMN IF NOT EXISTS legacy_designer_grandfathered"
            " boolean DEFAULT FALSE"
        )
        cursor.execute(
            "UPDATE extras_reporttemplate SET legacy_designer_grandfathered = FALSE"
            " WHERE legacy_designer_grandfathered IS NULL"
        )
        cursor.execute("ALTER TABLE extras_reporttemplate ALTER COLUMN legacy_designer_grandfathered SET DEFAULT FALSE")
        cursor.execute("ALTER TABLE extras_reporttemplate ALTER COLUMN legacy_designer_grandfathered SET NOT NULL")
    connection.commit()


def _restore_migration_head():
    """Return the shared test database to the true head after a rehearsal.

    A rehearsal that moved the schema below the head leaves the retirement
    migration recorded as applied while its columns linger and rows still
    record the removed legacy CSV shape. Clear the rehearsed flag and the
    simulated audit history that would restore it, then re-run the retirement
    migration so the database ends at the real migration head.
    """
    _ensure_retired_report_designer_columns()
    with connection.cursor() as cursor:
        cursor.execute("UPDATE extras_reporttemplate SET advanced_mode = FALSE")
        cursor.execute("DELETE FROM core_objectchange")
    connection.commit()
    MigrationRecorder(connection).record_unapplied("extras", "0127_retire_report_designer_legacy")
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


class ReportDesignerMigrationTests(TransactionTestCase):
    reset_sequences = True
    migrate_from = ("extras", "0102_alter_event_action")
    migrate_to = ("extras", "0105_reporttemplate_advanced_mode_and_more")

    def tearDown(self):
        try:
            _restore_migration_head()
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
        # 0105's reverse resets the marker before its own schema reconciliation,
        # so the rehearsal must start from the era's physical columns.
        _ensure_retired_report_designer_columns()
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


class FlagSuppressedScheduleTransitionTests(TransactionTestCase):
    """F2 regression: removing the designer flag must not resume deliveries.

    The removed worker guard skipped flag-disabled schedules without recording
    any marker, so an upgrade would have silently resumed outbound delivery
    for registered schedules. The 0123 migration pauses exactly that
    population (registered, active, non-grandfathered) on deployments that ran
    the designer disabled, and leaves every other population untouched.
    """

    # Sequences are intentionally not reset here: the seeded system schedules
    # are re-created by post-migrate handlers after every flush, so a
    # reset-to-1 would collide with the ids they have already re-issued.
    migrate_from = ("extras", "0122_journalentry_tenant_group")
    migrate_to = ("extras", "0123_pause_flag_suppressed_report_schedules")
    flag_names = ("ITAMBOX_FEATURE_REPORT_DESIGNER", "ITAMBOX_REPORT_DESIGNER_ENABLED")

    def tearDown(self):
        try:
            _restore_migration_head()
        finally:
            super().tearDown()

    def _historical_executor(self):
        # Keep this test focused on extras.0123; never reverse unrelated
        # irreversible cutovers just to reach the historical report state.
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

    @contextmanager
    def _flag_env(self, value):
        saved = {name: os.environ.get(name) for name in self.flag_names}
        try:
            for name in self.flag_names:
                os.environ.pop(name, None)
            if value is not None:
                os.environ[self.flag_names[0]] = value
            yield
        finally:
            for name, previous in saved.items():
                if previous is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = previous

    def _stage(self):
        """Reset extras to 0122 and create the transition scenarios."""
        MigrationRecorder(connection).record_unapplied("extras", "0123_pause_flag_suppressed_report_schedules")
        self.executor = self._historical_executor()
        self.executor.migrate([self.migrate_from])
        # The 0122-era scenario rows still write the retired columns on create.
        _ensure_retired_report_designer_columns()
        old_apps = self.executor.loader.project_state([self.migrate_from]).apps
        ReportTemplate = old_apps.get_model("extras", "ReportTemplate")
        ScheduledReport = old_apps.get_model("extras", "ScheduledReport")
        Tenant = old_apps.get_model("organization", "Tenant")
        Schedule = old_apps.get_model("django_q", "Schedule")
        tenant = Tenant.objects.create(name="Transition Tenant", slug="transition-tenant")

        suppressed_template = ReportTemplate.objects.create(
            name="suppressed", report_type="asset_summary", tenant=tenant
        )
        grandfathered_template = ReportTemplate.objects.create(
            name="grandfathered", report_type="asset_summary", tenant=tenant, legacy_designer_grandfathered=True
        )
        unscheduled_template = ReportTemplate.objects.create(
            name="unscheduled", report_type="asset_summary", tenant=tenant
        )
        inactive_template = ReportTemplate.objects.create(name="inactive", report_type="asset_summary", tenant=tenant)

        def q_schedule(name):
            return Schedule.objects.create(
                name=name,
                func="extras.tasks.reports.generate_scheduled_report_task",
                schedule_type="D",  # Schedule.DAILY; historical models drop class constants.
            )

        self.suppressed_q = q_schedule("scheduled_report_suppressed")
        self.grandfathered_q = q_schedule("scheduled_report_grandfathered")
        self.inactive_q = q_schedule("scheduled_report_inactive")

        # Flag-suppressed population: registered + active + non-grandfathered,
        # with delivery history from before the flag was disabled.
        self.suppressed = ScheduledReport.objects.create(
            name="suppressed",
            report=suppressed_template,
            tenant=tenant,
            is_active=True,
            schedule=self.suppressed_q,
            last_status="success",
            last_run=timezone.now(),
        )
        # Kept delivering under the disabled flag.
        self.grandfathered = ScheduledReport.objects.create(
            name="grandfathered",
            report=grandfathered_template,
            tenant=tenant,
            is_active=True,
            schedule=self.grandfathered_q,
        )
        # Active but never registered: no django-q row, nothing fires.
        self.unscheduled = ScheduledReport.objects.create(
            name="unscheduled", report=unscheduled_template, tenant=tenant, is_active=True
        )
        # Already paused: stays exactly as the operator left it.
        self.inactive = ScheduledReport.objects.create(
            name="inactive",
            report=inactive_template,
            tenant=tenant,
            is_active=False,
            schedule=self.inactive_q,
        )
        connection.commit()
        connection.close()

    def _migrate_forward(self):
        self.executor = self._historical_executor()
        self.executor.migrate([self.migrate_to])
        return self.executor.loader.project_state([self.migrate_to]).apps

    def test_disabled_designer_pauses_registered_active_non_grandfathered_schedules(self):
        self._stage()
        with self._flag_env(None):
            apps = self._migrate_forward()
        ScheduledReport = apps.get_model("extras", "ScheduledReport")
        Schedule = apps.get_model("django_q", "Schedule")

        suppressed = ScheduledReport.objects.get(name="suppressed")
        self.assertFalse(suppressed.is_active)
        self.assertIsNone(suppressed.schedule_id)
        # Historical state survives: delivery history is not rewritten.
        self.assertEqual(suppressed.last_status, "success")
        self.assertIsNotNone(suppressed.last_run)
        self.assertFalse(Schedule.objects.filter(pk=self.suppressed_q.pk).exists())

        grandfathered = ScheduledReport.objects.get(name="grandfathered")
        self.assertTrue(grandfathered.is_active)
        self.assertEqual(grandfathered.schedule_id, self.grandfathered_q.pk)
        self.assertTrue(Schedule.objects.filter(pk=self.grandfathered_q.pk).exists())

        unscheduled = ScheduledReport.objects.get(name="unscheduled")
        self.assertTrue(unscheduled.is_active)
        self.assertIsNone(unscheduled.schedule_id)

        inactive = ScheduledReport.objects.get(name="inactive")
        self.assertFalse(inactive.is_active)
        self.assertEqual(inactive.schedule_id, self.inactive_q.pk)
        self.assertTrue(Schedule.objects.filter(pk=self.inactive_q.pk).exists())

    def test_enabled_designer_keeps_every_schedule_delivering(self):
        self._stage()
        with self._flag_env("True"):
            apps = self._migrate_forward()
        ScheduledReport = apps.get_model("extras", "ScheduledReport")
        Schedule = apps.get_model("django_q", "Schedule")

        suppressed = ScheduledReport.objects.get(name="suppressed")
        self.assertTrue(suppressed.is_active)
        self.assertEqual(suppressed.schedule_id, self.suppressed_q.pk)
        self.assertTrue(Schedule.objects.filter(pk=self.suppressed_q.pk).exists())

    def test_reverse_is_refused(self):
        self._stage()
        with self._flag_env(None):
            self._migrate_forward()
        connection.close()
        self.executor = self._historical_executor()
        with self.assertRaises(RuntimeError) as caught:
            self.executor.migrate([self.migrate_from])
        self.assertIn("issue565.report_schedule_transition.reverse_refused", str(caught.exception))


class ReportDesignerRetirementMigrationTests(TransactionTestCase):
    """Rehearse issue #586 over the beta-era report-template schema."""

    reset_sequences = True
    migrate_from = ("extras", "0126_scheduledreport_last_run_archive")
    migrate_to = ("extras", "0127_retire_report_designer_legacy")
    migration_name = "0127_retire_report_designer_legacy"
    report_name_prefix = "issue586-retirement-"

    def setUp(self):
        super().setUp()
        self._restore_predecessor_schema()
        self._create_beta_templates()

    def tearDown(self):
        try:
            self._allow_forward_for_test_rows()
            connection.close()
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
        finally:
            super().tearDown()

    def _historical_executor(self):
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

    def _restore_predecessor_schema(self):
        MigrationRecorder(connection).record_unapplied("extras", self.migration_name)
        self.executor = self._historical_executor()
        self.executor.migrate([self.migrate_from])
        old_apps = self.executor.loader.project_state([self.migrate_from]).apps
        self.ReportTemplate = old_apps.get_model("extras", "ReportTemplate")
        _ensure_retired_report_designer_columns()

    def _create_beta_templates(self):
        # Derive the tenant from the same era snapshot as the report model; a
        # second project_state call would build different model classes and the
        # tenant foreign key would reject them.
        Tenant = self.ReportTemplate._meta.apps.get_model("organization", "Tenant")
        self.tenant = Tenant._base_manager.create(name="Issue 586 Tenant", slug="issue-586-tenant")

        def create(name, **fields):
            fields.setdefault("report_type", "asset_summary")
            fields.setdefault("tenant", self.tenant)
            return self.ReportTemplate._base_manager.create(name=f"{self.report_name_prefix}{name}", **fields)

        self.canonical = create("canonical")
        self.custom_html = create("custom-html", template_content="<h1>{{ report_name }}</h1>")
        self.marked = create("marked", legacy_designer_grandfathered=True)
        self.unsupported_a = create("unsupported-a", advanced_mode=True)
        self.unsupported_b = create("unsupported-b", advanced_mode=True)
        self.deleted_unsupported = create("deleted-unsupported", advanced_mode=True, deleted_at=timezone.now())
        connection.commit()
        connection.close()
        self.executor = self._historical_executor()

    def _allow_forward_for_test_rows(self):
        with connection.cursor() as cursor:
            columns = {
                column.name
                for column in connection.introspection.get_table_description(cursor, "extras_reporttemplate")
            }
            if "advanced_mode" in columns:
                cursor.execute(
                    "UPDATE extras_reporttemplate SET advanced_mode = FALSE WHERE name LIKE %s",
                    [f"{self.report_name_prefix}%"],
                )
        connection.commit()

    def _migrate_forward(self):
        connection.close()
        self.executor = self._historical_executor()
        self.executor.migrate([self.migrate_to])
        return self.executor.loader.project_state([self.migrate_to]).apps

    def _column_names(self):
        with connection.cursor() as cursor:
            return {
                column.name
                for column in connection.introspection.get_table_description(cursor, "extras_reporttemplate")
            }

    def test_live_legacy_csv_shapes_refuse_before_schema_or_data_changes(self):
        before = list(
            self.ReportTemplate._base_manager.filter(name__startswith=self.report_name_prefix)
            .order_by("name", "pk")
            .values_list(
                "pk",
                "name",
                "advanced_mode",
                "template_content",
                "legacy_designer_grandfathered",
                "deleted_at",
            )
        )

        with self.assertRaises(RuntimeError) as caught:
            self._migrate_forward()

        message = str(caught.exception)
        self.assertTrue(message.startswith("issue586.report_designer_legacy.legacy_csv_shape_unsupported"))
        first = f"{self.unsupported_a.name} (pk={self.unsupported_a.pk})"
        second = f"{self.unsupported_b.name} (pk={self.unsupported_b.pk})"
        self.assertIn(first, message)
        self.assertIn(second, message)
        self.assertLess(message.index(first), message.index(second))
        self.assertNotIn(self.deleted_unsupported.name, message)
        self.assertTrue({"advanced_mode", "legacy_designer_grandfathered"} <= self._column_names())
        self.assertEqual(
            list(
                self.ReportTemplate._base_manager.filter(name__startswith=self.report_name_prefix)
                .order_by("name", "pk")
                .values_list(
                    "pk",
                    "name",
                    "advanced_mode",
                    "template_content",
                    "legacy_designer_grandfathered",
                    "deleted_at",
                )
            ),
            before,
        )
        self.assertFalse(
            MigrationRecorder(connection).migration_qs.filter(app="extras", name=self.migration_name).exists()
        )

    def test_refused_migration_retries_after_resolving_flags_and_keeps_canonical_data(self):
        with self.assertRaises(RuntimeError):
            self._migrate_forward()

        self.ReportTemplate._base_manager.filter(pk__in=[self.unsupported_a.pk, self.unsupported_b.pk]).update(
            advanced_mode=False
        )
        apps = self._migrate_forward()
        ReportTemplate = apps.get_model("extras", "ReportTemplate")
        rows = {row.name: row for row in ReportTemplate._base_manager.filter(name__startswith=self.report_name_prefix)}

        self.assertEqual(rows[self.canonical.name].template_content, "")
        self.assertEqual(rows[self.custom_html.name].template_content, "<h1>{{ report_name }}</h1>")
        self.assertEqual(rows[self.marked.name].template_content, "")
        self.assertNotIn("advanced_mode", {field.name for field in ReportTemplate._meta.local_fields})
        self.assertNotIn("legacy_designer_grandfathered", {field.name for field in ReportTemplate._meta.local_fields})
        self.assertIn("template_content", {field.name for field in ReportTemplate._meta.local_fields})
        self.assertFalse({"advanced_mode", "legacy_designer_grandfathered"} & self._column_names())

    def test_soft_deleted_legacy_csv_shape_does_not_block_migration(self):
        self.ReportTemplate._base_manager.filter(pk__in=[self.unsupported_a.pk, self.unsupported_b.pk]).update(
            advanced_mode=False
        )

        apps = self._migrate_forward()
        ReportTemplate = apps.get_model("extras", "ReportTemplate")

        self.assertFalse({"advanced_mode", "legacy_designer_grandfathered"} & self._column_names())
        self.assertTrue(ReportTemplate._base_manager.filter(pk=self.deleted_unsupported.pk).exists())
        self.assertTrue(
            ReportTemplate._base_manager.filter(pk=self.deleted_unsupported.pk, deleted_at__isnull=False).exists()
        )

    def test_empty_report_table_migrates_cleanly_to_the_new_head(self):
        self.ReportTemplate._base_manager.filter(name__startswith=self.report_name_prefix).delete()

        apps = self._migrate_forward()

        ReportTemplate = apps.get_model("extras", "ReportTemplate")
        self.assertFalse(ReportTemplate._base_manager.filter(name__startswith=self.report_name_prefix).exists())
        self.assertFalse({"advanced_mode", "legacy_designer_grandfathered"} & self._column_names())

    def test_reverse_is_refused_and_schema_stays_at_the_new_head(self):
        self.ReportTemplate._base_manager.filter(pk__in=[self.unsupported_a.pk, self.unsupported_b.pk]).update(
            advanced_mode=False
        )
        self._migrate_forward()
        connection.close()
        self.executor = self._historical_executor()

        with self.assertRaises(RuntimeError) as caught:
            self.executor.migrate([self.migrate_from])

        self.assertTrue(str(caught.exception).startswith("issue586.report_designer_legacy.reverse_refused"))
        self.assertFalse({"advanced_mode", "legacy_designer_grandfathered"} & self._column_names())
        self.assertTrue(
            MigrationRecorder(connection).migration_qs.filter(app="extras", name=self.migration_name).exists()
        )
