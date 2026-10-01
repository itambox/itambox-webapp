"""Upgrade/rollback rehearsal for the Scheduled Reports Stable promotion.

The 0124 transition must preserve every pre-promotion population while it
backfills the fire-identity kwarg and reconciles duplicate registration rows
onto the row the schedule references; 0125 then registers the newest accepted
occurrence as a fire record and adds the delivery-retry hardening columns to
the generation archive; 0126 binds Retry delivery to the newest run's archive
without backfilling pre-promotion rows.
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
from django.utils import timezone

REPORT_TASK_PATH = "extras.tasks.reports.generate_scheduled_report_task"
FIRE_KWARG = "intended_fire_at"
FIRE_IDENTITY_MIGRATION = ("extras", "0124_scheduled_report_fire_identity_delivery_outcomes")
HARDENING_MIGRATION = ("extras", "0125_scheduled_report_fire_records_retry_hardening")
ARCHIVE_TABLE = "extras_reportgenerationarchive"
FIRE_TABLE = "extras_scheduledreportfire"
SCHEDULE_TABLE = "extras_scheduledreport"


class ScheduledReportFireIdentityTransitionTests(TransactionTestCase):
    """Upgrade evidence for 0124-0126 (fire identity, ledger, retry hardening and binding)."""

    reset_sequences = True
    migrate_from = ("extras", "0123_pause_flag_suppressed_report_schedules")
    migrate_to = ("extras", "0126_scheduledreport_last_run_archive")

    def tearDown(self):
        # Restore the shared test database to the leaf state so later tests
        # never see a rehearsed (partially migrated) schema. Re-run the
        # retirement migration above the 0126 rehearsal so the columns it
        # removes at the head do not linger once the rehearsal moved the
        # schema back below the head.
        try:
            self._ensure_retired_report_designer_columns()
            MigrationRecorder(connection).record_unapplied("extras", "0127_retire_report_designer_legacy")
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
        finally:
            super().tearDown()

    def _ensure_retired_report_designer_columns(self):
        """Reconcile the columns the 0127 retirement removes at the migration head.

        The 0123 rehearsal below stages its scenarios on the schema of its era,
        which still carries the two retired columns physically, and the
        0123-era model writes them on create. Restore them explicitly
        (idempotent, mirroring 0105's own persistent-field reconciliation).
        """
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE extras_reporttemplate ADD COLUMN IF NOT EXISTS advanced_mode boolean DEFAULT FALSE"
            )
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
            cursor.execute(
                "ALTER TABLE extras_reporttemplate ALTER COLUMN legacy_designer_grandfathered SET DEFAULT FALSE"
            )
            cursor.execute("ALTER TABLE extras_reporttemplate ALTER COLUMN legacy_designer_grandfathered SET NOT NULL")
        connection.commit()

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
        if "delivery_status" in self._columns(ARCHIVE_TABLE):
            # An earlier interrupted rehearsal can leave the physical schema
            # ahead of the recorder. Align each transition as applied to the
            # columns it added so the backward migration below really reverses
            # them instead of tripping over already-existing columns.
            applied = recorder.applied_migrations()
            for key in (self.migrate_from, FIRE_IDENTITY_MIGRATION):
                if key not in applied:
                    recorder.record_applied(*key)
            if "generation_scope" in self._columns(ARCHIVE_TABLE) and HARDENING_MIGRATION not in applied:
                recorder.record_applied(*HARDENING_MIGRATION)
            if "last_run_archive_id" in self._columns(SCHEDULE_TABLE) and self.migrate_to not in applied:
                recorder.record_applied(*self.migrate_to)
        self.executor = self._historical_executor()
        self.executor.migrate([self.migrate_from])
        old_apps = self.executor.loader.project_state([self.migrate_from]).apps
        self.ScheduledReport = old_apps.get_model("extras", "ScheduledReport")
        self.ReportGeneratorArchive = old_apps.get_model("extras", "ReportGenerationArchive")
        self.ScopeAuthorization = old_apps.get_model("extras", "ScheduledReportScopeAuthorization")
        self.Tenant = old_apps.get_model("organization", "Tenant")
        self.Schedule = old_apps.get_model("django_q", "Schedule")

        # The 0123-era model still writes the retired designer columns on create.
        self._ensure_retired_report_designer_columns()
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

        # Unreferenced duplicates (no ScheduledReport references either row):
        # the oldest row survives deterministically.
        self.orphan_a = self.Schedule.objects.create(
            name="scheduled_report_orphan", func=REPORT_TASK_PATH, schedule_type="H", intended_date_kwarg=""
        )
        self.orphan_b = self.Schedule.objects.create(
            name="scheduled_report_orphan", func=REPORT_TASK_PATH, schedule_type="H", intended_date_kwarg=""
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

    def _tables(self):
        return set(connection.introspection.table_names())

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

    def test_upgrade_keeps_the_referenced_row_when_collapsing_duplicates(self):
        self._stage()
        apps = self._migrate_forward()
        Schedule = apps.get_model("django_q", "Schedule")
        ScheduledReport = apps.get_model("extras", "ScheduledReport")

        rows = list(Schedule.objects.filter(name="scheduled_report_dup"))
        self.assertEqual(len(rows), 1)
        keeper = rows[0]
        # The referenced row survives: it carries the live next_run/cadence
        # state, and an older unreferenced duplicate must never replace the
        # schedule the operator is running.
        self.assertEqual(keeper.pk, self.dup_b.pk)
        self.assertEqual(keeper.intended_date_kwarg, FIRE_KWARG)
        self.assertFalse(Schedule.objects.filter(pk=self.dup_a.pk).exists())

        # The schedule FK still points at the surviving, referenced row.
        schedule = ScheduledReport.objects.get(pk=self.dup_schedule.pk)
        self.assertEqual(schedule.schedule_id, keeper.pk)
        # Collapsing the duplicates did not touch activation state or history.
        self.assertTrue(schedule.is_active)
        self.assertEqual(schedule.last_status, "success")
        # Foreign rows are never collapsed.
        self.assertTrue(Schedule.objects.filter(pk=self.foreign_row.pk).exists())

    def test_upgrade_collapses_unreferenced_duplicates_onto_the_oldest(self):
        self._stage()
        apps = self._migrate_forward()
        Schedule = apps.get_model("django_q", "Schedule")

        rows = list(Schedule.objects.filter(name="scheduled_report_orphan"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].pk, self.orphan_a.pk)
        self.assertFalse(Schedule.objects.filter(pk=self.orphan_b.pk).exists())

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
        # The retry binding is not backfilled either: legacy rows keep a null
        # archive reference so their retry action cannot fire against an
        # older archived report.
        self.assertIsNone(ScheduledReport.objects.get(pk=self.legacy_schedule.pk).last_run_archive_id)

        # Approval and archive records survive with unchanged semantics.
        approval = Authorization.objects.get(pk=self.approval.pk)
        self.assertEqual(approval.scope_tenant_ids, [self.tenant.pk])
        self.assertIsNone(approval.revoked_at)
        archive = Archive.objects.get(pk=self.archive.pk)
        self.assertEqual(archive.status, "success")
        self.assertEqual(archive.delivery_status, "")
        self.assertEqual(archive.delivery_targets, [])
        self.assertEqual(archive.disclosure_text, "")
        # Retry hardening arrives inert: no scope snapshot, no held claim.
        self.assertEqual(archive.generation_scope, {})
        self.assertEqual(archive.retry_claim_token, "")
        self.assertIsNone(archive.retry_claim_expires_at)
        # And no fire records are invented for rows without an accepted marker.
        Fire = apps.get_model("extras", "ScheduledReportFire")
        self.assertEqual(Fire.objects.count(), 0)

    def test_upgrade_backfills_fire_records_from_the_high_water_marker(self):
        self._stage()
        # Land on 0124 first: the marker and the fire records only coexist
        # through this intermediate state.
        self.executor = self._historical_executor()
        self.executor.migrate([FIRE_IDENTITY_MIGRATION])
        identity_apps = self.executor.loader.project_state([FIRE_IDENTITY_MIGRATION]).apps
        ScheduledReport = identity_apps.get_model("extras", "ScheduledReport")
        marker = timezone.now().replace(microsecond=0)
        ScheduledReport.objects.filter(pk=self.legacy_schedule.pk).update(last_accepted_fire_at=marker)

        apps = self._migrate_forward()
        Fire = apps.get_model("extras", "ScheduledReportFire")
        fires = list(Fire.objects.filter(schedule_id=self.legacy_schedule.pk))
        self.assertEqual(len(fires), 1)
        self.assertEqual(fires[0].intended_fire_at, marker)
        # Schedules without a marker get no synthetic fire record.
        self.assertEqual(Fire.objects.filter(schedule_id=self.paused.pk).count(), 0)

        # Reverse 0125: the fire records travel with the table it created and
        # the marker itself is preserved for a later forward.
        self.executor = self._historical_executor()
        self.executor.migrate([FIRE_IDENTITY_MIGRATION])
        self.assertNotIn(FIRE_TABLE, self._tables())
        ScheduledReport = self.executor.loader.project_state([FIRE_IDENTITY_MIGRATION]).apps.get_model(
            "extras", "ScheduledReport"
        )
        self.assertEqual(ScheduledReport.objects.get(pk=self.legacy_schedule.pk).last_accepted_fire_at, marker)

        # Forward again: the fire record is restored from the marker.
        apps = self._migrate_forward()
        Fire = apps.get_model("extras", "ScheduledReportFire")
        self.assertEqual(Fire.objects.filter(schedule_id=self.legacy_schedule.pk).count(), 1)

    def test_forward_reverse_forward_keeps_schema_and_kwarg_semantics(self):
        self._stage()

        archive_table = ARCHIVE_TABLE
        self._migrate_forward()
        self.assertIn("delivery_status", self._columns(archive_table))
        self.assertIn("delivery_targets", self._columns(archive_table))
        self.assertIn("disclosure_text", self._columns(archive_table))
        for column in ("generation_scope", "retry_claim_token", "retry_claim_expires_at"):
            self.assertIn(column, self._columns(archive_table))
        self.assertIn(FIRE_TABLE, self._tables())
        self.assertIn("last_run_archive_id", self._columns(SCHEDULE_TABLE))
        bound_report = self.executor.loader.project_state([self.migrate_to]).apps.get_model("extras", "ScheduledReport")
        self.assertIsNone(bound_report.objects.get(pk=self.legacy_schedule.pk).last_run_archive_id)
        Schedule = self.executor.loader.project_state([self.migrate_to]).apps.get_model("django_q", "Schedule")
        self.assertEqual(Schedule.objects.get(pk=self.legacy_row.pk).intended_date_kwarg, FIRE_KWARG)

        # Roll back to the predecessor: the kwarg must be cleared (an old task
        # signature would reject it) and every promotion column and table must
        # disappear, including the 0125 fire records and retry-hardening fields.
        # Rebuild the executor so its loader sees the freshly applied leaf.
        self.executor = self._historical_executor()
        self.executor.migrate([self.migrate_from])
        self.assertNotIn("delivery_status", self._columns(archive_table))
        self.assertNotIn("generation_scope", self._columns(archive_table))
        self.assertNotIn("retry_claim_token", self._columns(archive_table))
        self.assertNotIn("last_run_archive_id", self._columns(SCHEDULE_TABLE))
        self.assertNotIn(FIRE_TABLE, self._tables())
        Schedule = self.executor.loader.project_state([self.migrate_from]).apps.get_model("django_q", "Schedule")
        self.assertEqual(Schedule.objects.get(pk=self.legacy_row.pk).intended_date_kwarg, "")

        # And forward again: kwarg returns, state intact.
        apps = self._migrate_forward()
        Schedule = apps.get_model("django_q", "Schedule")
        self.assertEqual(Schedule.objects.get(pk=self.legacy_row.pk).intended_date_kwarg, FIRE_KWARG)
        self.assertIn("delivery_status", self._columns(archive_table))
        self.assertIn("last_run_archive_id", self._columns(SCHEDULE_TABLE))
        self.assertIn(FIRE_TABLE, self._tables())
        ScheduledReport = apps.get_model("extras", "ScheduledReport")
        self.assertTrue(ScheduledReport.objects.get(pk=self.legacy_schedule.pk).is_active)
        self.assertFalse(ScheduledReport.objects.get(pk=self.paused.pk).is_active)
