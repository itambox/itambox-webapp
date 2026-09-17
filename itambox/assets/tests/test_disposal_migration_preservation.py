"""#496: migration 0118 preserves existing disposal evidence and adds uniqueness.

The pre-0118 relation is one-to-one, so a legacy database cannot hold two rows for the
same asset. This module rehearses the upgrade on real legacy rows -- one active, one
soft-deleted -- and proves that identifiers, evidence and relations survive, that the
new conditional unique constraint is enforced afterwards, and that the control insert
uses the same shape (so a constraint violation is not a malformed-INSERT false positive).

Run against an isolated database, e.g.
``TEST_DATABASE_NAME=issue496_migration_testing pytest ... --create-db``.
"""

import datetime
import importlib
import uuid

import pytest
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.graph import MigrationGraph
from django.test import TransactionTestCase
from django.utils import timezone

MIGRATE_TO = ("assets", "0118_assetdisposal_cancellation_reason_and_more")


@pytest.mark.serial_only
class DisposalMigrationPreservationTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        # Restore the shared schema to the migration leaf even if this fixture raises.
        self.addCleanup(self._restore_leaf)
        executor = MigrationExecutor(connection)
        self.migrate_from = next(
            dependency
            for dependency in executor.loader.disk_migrations[MIGRATE_TO].dependencies
            if dependency[0] == "assets"
        )
        self.executor = self._scoped_executor()
        self.executor.migrate([self.migrate_from])

    def _scoped_executor(self):
        """Executor restricted to 0118's forward plan, so unrelated migrations stay applied."""
        executor = MigrationExecutor(connection)
        loader = executor.loader
        allowed = set(loader.graph.forwards_plan(MIGRATE_TO))
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

    def _restore_leaf(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_0118_operation_shape_is_additive_only(self):
        """Shape check, not preservation proof: no operation can rewrite existing values."""
        module = importlib.import_module("assets.migrations.0118_assetdisposal_cancellation_reason_and_more")
        operation_names = {operation.__class__.__name__ for operation in module.Migration.operations}
        self.assertTrue(operation_names <= {"AddField", "AlterField", "AddConstraint"}, operation_names)

    def test_0118_preserves_legacy_rows_and_arbitrates_active_uniqueness(self):
        old_apps = self.executor.loader.project_state([self.migrate_from]).apps
        Tenant = old_apps.get_model("organization", "Tenant")
        StatusLabel = old_apps.get_model("assets", "StatusLabel")
        Asset = old_apps.get_model("assets", "Asset")
        LegacyDisposal = old_apps.get_model("assets", "AssetDisposal")

        # The historical state must really be pre-0118: no cancellation fields yet, and
        # the legacy manager set is whatever that migration state declared.
        historical_fields = {field.name for field in LegacyDisposal._meta.get_fields()}
        for added in ("cancelled_at", "cancelled_by", "cancellation_reason"):
            self.assertNotIn(added, historical_fields)
        self.assertIn("deleted_at", historical_fields)
        legacy_manager = LegacyDisposal._default_manager
        self.assertFalse(hasattr(LegacyDisposal, "all_objects"))
        method = LegacyDisposal._meta.get_field("disposal_method").choices[0][0]

        suffix = uuid.uuid4().hex[:8]
        tenant = Tenant.objects.create(name=f"Migration tenant {suffix}", slug=f"migration-tenant-{suffix}")
        archived = StatusLabel.objects.create(name=f"Archived {suffix}", type="archived")
        live_asset = Asset.objects.create(
            asset_tag=f"MIG-496-A-{suffix}", name="Live laptop", status=archived, tenant=tenant
        )
        stored_asset = Asset.objects.create(
            asset_tag=f"MIG-496-B-{suffix}", name="Recycle bin laptop", status=archived, tenant=tenant
        )
        control_asset = Asset.objects.create(
            asset_tag=f"MIG-496-C-{suffix}", name="Control laptop", status=archived, tenant=tenant
        )

        deleted_at = timezone.now()
        live_row = legacy_manager.create(
            asset=live_asset, disposal_method=method, disposal_date=datetime.date(2026, 5, 1)
        )
        stored_row = legacy_manager.create(
            asset=stored_asset,
            disposal_method=method,
            disposal_date=datetime.date(2026, 5, 2),
            deleted_at=deleted_at,
        )
        live_id, stored_id = live_row.pk, stored_row.pk

        executor = MigrationExecutor(connection)
        executor.migrate([MIGRATE_TO])

        from assets.models import AssetDisposal as CurrentDisposal

        migrated_live = CurrentDisposal.all_objects.get(pk=live_id)
        migrated_stored = CurrentDisposal.all_objects.get(pk=stored_id)
        self.assertEqual(migrated_live.asset_id, live_asset.pk)
        self.assertEqual(migrated_stored.asset_id, stored_asset.pk)
        self.assertEqual(migrated_live.disposal_date, datetime.date(2026, 5, 1))
        self.assertEqual(migrated_stored.disposal_date, datetime.date(2026, 5, 2))
        self.assertEqual(migrated_stored.deleted_at, deleted_at)
        self.assertIsNone(migrated_live.cancelled_at)
        self.assertIsNone(migrated_stored.cancelled_at, "hidden evidence must not be silently cancelled")
        self.assertIsNone(migrated_live.cancelled_by_id)
        self.assertEqual(migrated_live.cancellation_reason, "")

        # Uniqueness after the upgrade: a second active row is rejected for the live asset
        # and, equally important, for the asset whose only record is soft-deleted.
        for asset_id, day in ((live_asset.pk, 20), (stored_asset.pk, 21)):
            with self.assertRaises(IntegrityError) as clash:
                CurrentDisposal.objects.create(
                    asset_id=asset_id, disposal_method=method, disposal_date=datetime.date(2026, 6, day)
                )
            self.assertIn("uniq_active_disposal_per_asset", str(clash.exception))

        # Control: the identical insert shape succeeds for an asset without a record, so
        # the failures above are the constraint and not a malformed INSERT.
        control = CurrentDisposal.objects.create(
            asset_id=control_asset.pk, disposal_method=method, disposal_date=datetime.date(2026, 6, 22)
        )
        self.assertIsNotNone(control.pk)
        self.assertEqual(
            CurrentDisposal.all_objects.filter(cancelled_at__isnull=True).count(),
            CurrentDisposal.all_objects.count(),
        )
