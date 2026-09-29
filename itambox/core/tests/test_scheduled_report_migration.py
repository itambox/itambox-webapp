"""Upgrade/rollback rehearsal for the Scheduled Reports Stable promotion.

The 0124 transition must preserve every pre-promotion population while it
backfills the fire-identity kwarg and reconciles duplicate registration rows.
The rehearsal drives a real ``MigrationExecutor`` against the shared test
schema: stage at 0123, build the pre-upgrade state, migrate forward, and
inspect the post-upgrade state through historical models, then verify the
forward/reverse/forward round trip.
"""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.graph import MigrationGraph
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase

REPORT_TASK_PATH = "extras.tasks.reports.generate_scheduled_report_task"
FIRE_KWARG = "intended_fire_at"


class ScheduledReportFireIdentityTransitionTests(TransactionTestCase):
    """Upgrade evidence for 0124 (fire identity, delivery ledger, dedupe)."""

    reset_sequences = True
    migrate_from = ("extras", "0123_pause_flag_suppressed_report_schedules")
    migrate_to = ("extras", "0124_scheduled_report_fire_identity_delivery_outcomes")

    def tearDown(self):
        # Restore the shared test database to the leaf state so later tests
        # never see a rehearsed (partially migrated) schema.
        try:
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

    def _stage(self):
        """Reset extras to 0123 and create the pre-upgrade scenarios."""
        recorder = MigrationRecorder(connection)
        if "delivery_status" in self._columns("extras_reportgenerationarchive"):
            # An earlier interrupted rehearsal can leave the physical schema
            # ahead of the recorder. Align both transitions as applied so the
            # backward migration below really reverses 0124 instead of
            # tripping over already-existing columns.
            applied = recorder.applied_migrations()
            for key in (self.migrate_from, self.migrate_to):
                if key not in applied:
                    recorder.record_applied(*key)
        self.executor = self._historical_executor()
        self.executor.migrate([self.migrate_from])
        old_apps = self.executor.loader.project_state([self.migrate_from]).apps
        self.ScheduledReport = old_apps.get_model("extras", "ScheduledReport")
        self.ReportGeneratorArchive = old_apps.get_model("extras", "ReportGenerationArchive")
        self.ScopeAuthorization = old_apps.get_model("extras", "ScheduledReportScopeAuthorization")
        self.Tenant = old_apps.get_model("organization", "Tenant")
        self.Schedule = old_apps.get_model("django_q", "Schedule")

        tenant = self.Tenant.objects.create(name="Rehearsal Tenant", slug="rehearsal-tenant")
        report = old_apps.get_model("extras", "ReportTemplate").objects.create(
            name="rehearsal", report_type="asset_summary", tenant=tenant
        )

        self.tenant = tenant
        self.report = report

        # The deployment registers system schedules during post_migrate; start
        # from a clean queue table so the rehearsal fixture controls every row
        # (and sequences reset per test cannot collide with leftover rows).
        self.Schedule.objects.all().delete()

        # An unrelated schedule: must stay untouched by the transition.
        self.foreign_row = self.Schedule.objects.create(
            name="Daily Alert Rule Evaluation",
            func="extras.tasks.alerts.evaluate_alert_rules_task",
            schedule_type="D",
            intended_date_kwarg="",
        )
        # A report schedule row that predates the kwarg injection.
        self.legacy_row = self.Schedule.objects.create(
            name="scheduled_report_legacy",
            func=REPORT_TASK_PATH,
            schedule_type="H",
            intended_date_kwarg="",
        )

        self.legacy_schedule = self.ScheduledReport.objects.create(
            name="legacy",
            report=report,
            tenant=tenant,
            is_active=True,
            schedule=self.legacy_row,
            last_status="success",
            last_run=None,
        )

        # Duplicate registration rows for one schedule (pre-advisory-lock code
        # could create these), with the FK pointing at the newest row.
        self.dup_a = self.Schedule.objects.create(
            name="scheduled_report_dup", func=REPORT_TASK_PATH, schedule_type="H", intended_date_kwarg=""
        )
        self.dup_b = self.Schedule.objects.create(
            name="scheduled_report_dup", func=REPORT_TASK_PATH, schedule_type="H", intended_date_kwarg=""
        )
        self.dup_schedule = self.ScheduledReport.objects.create(
            name="dup",
            report=report,
            tenant=tenant,
            is_active=True,
            schedule=self.dup_b,
            last_status="success",
            last_run=None,
        )

        # A paused (dormant) schedule: must stay paused and un-resumed.
        self.paused_q = self.Schedule.objects.create(
            name="scheduled_report_paused",
            func=REPORT_TASK_PATH,
            schedule_type="H",
            intended_date_kwarg="",
            next_run=None,
        )
        self.paused = self.ScheduledReport.objects.create(
            name="paused",
            report=report,
            tenant=tenant,
            is_active=False,
            schedule=self.paused_q,
            last_status="failed",
            last_run=None,
        )

        # Cross-tenant approval and archive rows: preserved byte-for-byte.
        # The approver is created through the live model (the users schema is
        # never migrated backwards here); the historical approval row only
        # needs the id.
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create(username="rehearsal-approver", email="approver@example.com")
        self.approval = self.ScopeAuthorization.objects.create(
            scheduled_report=self.dup_schedule,
            authorized_by_id=user.pk,
            scope_tenant_ids=[tenant.pk],
            revoked_at=None,
        )
        self.archive = self.ReportGeneratorArchive.objects.create(
            scheduled_report=self.legacy_schedule,
            tenant=tenant,
            status="success",
            format="html",
        )

    def _migrate_forward(self):
        self.executor = self._historical_executor()
        self.executor.migrate([self.migrate_to])
        return self.executor.loader.project_state([self.migrate_to]).apps

    def _columns(self, table):
        with connection.cursor() as cursor:
            return {column.name for column in connection.introspection.get_table_description(cursor, table)}

    def test_upgrade_backfills_the_fire_kwarg_on_report_rows_only(self):
        self._stage()
        apps = self._migrate_forward()
        Schedule = apps.get_model("django_q", "Schedule")

        self.assertEqual(Schedule.objects.get(pk=self.legacy_row.pk).intended_date_kwarg, FIRE_KWARG)
        self.assertEqual(Schedule.objects.get(pk=self.paused_q.pk).intended_date_kwarg, FIRE_KWARG)
        self.assertEqual(Schedule.objects.get(pk=self.foreign_row.pk).intended_date_kwarg, "")
        # Nothing else about the row may change.
        row = Schedule.objects.get(pk=self.legacy_row.pk)
        self.assertEqual(row.name, "scheduled_report_legacy")
        self.assertEqual(row.schedule_type, "H")
        self.assertEqual(row.repeats, -1)

    def test_upgrade_collapses_duplicate_rows_and_repoints_the_schedule(self):
        self._stage()
        apps = self._migrate_forward()
        Schedule = apps.get_model("django_q", "Schedule")
        ScheduledReport = apps.get_model("extras", "ScheduledReport")

        rows = list(Schedule.objects.filter(name="scheduled_report_dup"))
        self.assertEqual(len(rows), 1)
        keeper = rows[0]
        self.assertEqual(keeper.pk, self.dup_a.pk)
        self.assertEqual(keeper.intended_date_kwarg, FIRE_KWARG)
        self.assertFalse(Schedule.objects.filter(pk=self.dup_b.pk).exists())

        # The schedule FK was re-pointed at the surviving row.
        schedule = ScheduledReport.objects.get(pk=self.dup_schedule.pk)
        self.assertEqual(schedule.schedule_id, keeper.pk)
        # Deactivating the duplicates did not touch activation state or history.
        self.assertTrue(schedule.is_active)
        self.assertEqual(schedule.last_status, "success")
        # Foreign rows are never collapsed.
        self.assertTrue(Schedule.objects.filter(pk=self.foreign_row.pk).exists())

    def test_upgrade_preserves_paused_state_approvals_and_archives(self):
        self._stage()
        apps = self._migrate_forward()
        Schedule = apps.get_model("django_q", "Schedule")
        ScheduledReport = apps.get_model("extras", "ScheduledReport")
        Archive = apps.get_model("extras", "ReportGenerationArchive")
        Authorization = apps.get_model("extras", "ScheduledReportScopeAuthorization")

        # Paused schedule: dormant before, dormant after; row kept; no next_run.
        paused = ScheduledReport.objects.get(pk=self.paused.pk)
        self.assertFalse(paused.is_active)
        self.assertEqual(paused.last_status, "failed")
        self.assertEqual(Schedule.objects.get(pk=self.paused_q.pk).next_run, None)

        # Fire identity is not backfilled with a synthetic time.
        self.assertIsNone(ScheduledReport.objects.get(pk=self.legacy_schedule.pk).last_accepted_fire_at)

        # Approval and archive records survive with unchanged semantics.
        approval = Authorization.objects.get(pk=self.approval.pk)
        self.assertEqual(approval.scope_tenant_ids, [self.tenant.pk])
        self.assertIsNone(approval.revoked_at)
        archive = Archive.objects.get(pk=self.archive.pk)
        self.assertEqual(archive.status, "success")
        self.assertEqual(archive.delivery_status, "")
        self.assertEqual(archive.delivery_targets, [])
        self.assertEqual(archive.disclosure_text, "")

    def test_forward_reverse_forward_keeps_schema_and_kwarg_semantics(self):
        self._stage()

        archive_table = "extras_reportgenerationarchive"
        self._migrate_forward()
        self.assertIn("delivery_status", self._columns(archive_table))
        self.assertIn("delivery_targets", self._columns(archive_table))
        self.assertIn("disclosure_text", self._columns(archive_table))
        Schedule = self.executor.loader.project_state([self.migrate_to]).apps.get_model("django_q", "Schedule")
        self.assertEqual(Schedule.objects.get(pk=self.legacy_row.pk).intended_date_kwarg, FIRE_KWARG)

        # Roll back to the predecessor: the kwarg must be cleared (an old task
        # signature would reject it) and the ledger columns must disappear.
        # Rebuild the executor so its loader sees the freshly applied 0124.
        self.executor = self._historical_executor()
        self.executor.migrate([self.migrate_from])
        self.assertNotIn("delivery_status", self._columns(archive_table))
        Schedule = self.executor.loader.project_state([self.migrate_from]).apps.get_model("django_q", "Schedule")
        self.assertEqual(Schedule.objects.get(pk=self.legacy_row.pk).intended_date_kwarg, "")

        # And forward again: kwarg returns, state intact.
        apps = self._migrate_forward()
        Schedule = apps.get_model("django_q", "Schedule")
        self.assertEqual(Schedule.objects.get(pk=self.legacy_row.pk).intended_date_kwarg, FIRE_KWARG)
        self.assertIn("delivery_status", self._columns(archive_table))
        ScheduledReport = apps.get_model("extras", "ScheduledReport")
        self.assertTrue(ScheduledReport.objects.get(pk=self.legacy_schedule.pk).is_active)
        self.assertFalse(ScheduledReport.objects.get(pk=self.paused.pk).is_active)
