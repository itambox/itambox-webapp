import pytest
from django.apps import apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.graph import MigrationGraph
from django.test import TransactionTestCase

from assets.models import StatusLabel
from assets.signals import ensure_canonical_missing_status


@pytest.mark.serial_only
class CanonicalMissingStatusMigrationTests(TransactionTestCase):
    migrate_from = ("assets", "0100_issue88_shard_43_assets_seed")
    migrate_to = ("assets", "0101_seed_canonical_missing_status")

    def tearDown(self):
        # Restore the shared test database to the migration leaf state so later
        # tests never see a rehearsed (partially migrated) schema.
        try:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
        finally:
            super().tearDown()

    def _historical_executor(self):
        # Keep this test focused on the assets.0101 status seed: only the nodes
        # on its dependency plan are migrated, so unrelated irreversible
        # migrations (for example extras.0116 lifecycle normalization) are
        # never reversed just to reach the historical state.
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

    def test_forward_reverse_forward_is_additive_and_idempotent(self):
        executor = self._historical_executor()
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        StatusLabel = old_apps.get_model("assets", "StatusLabel")
        StatusLabel.objects.filter(slug="missing").delete()
        self.assertFalse(StatusLabel.objects.filter(slug="missing").exists())

        executor = self._historical_executor()
        executor.migrate([self.migrate_to])
        migrated_apps = executor.loader.project_state([self.migrate_to]).apps
        StatusLabel = migrated_apps.get_model("assets", "StatusLabel")
        missing = StatusLabel.objects.get(slug="missing")
        self.assertEqual((missing.name, missing.type, missing.color), ("Missing", "undeployable", "dc3545"))

        executor = self._historical_executor()
        executor.migrate([self.migrate_from])
        reversed_apps = executor.loader.project_state([self.migrate_from]).apps
        StatusLabel = reversed_apps.get_model("assets", "StatusLabel")
        self.assertEqual(StatusLabel.objects.filter(slug="missing").count(), 1)

        executor = self._historical_executor()
        executor.migrate([self.migrate_to])
        reapplied_apps = executor.loader.project_state([self.migrate_to]).apps
        StatusLabel = reapplied_apps.get_model("assets", "StatusLabel")
        self.assertEqual(StatusLabel.objects.filter(slug="missing").count(), 1)


@pytest.mark.serial_only
class CanonicalMissingPostMigrateTests(TransactionTestCase):
    def test_post_migrate_restores_reference_row_after_flush(self):
        StatusLabel._base_manager.filter(slug="missing").delete()
        self.assertFalse(StatusLabel._base_manager.filter(slug="missing").exists())

        ensure_canonical_missing_status(sender=apps.get_app_config("assets"), using=connection.alias)

        missing = StatusLabel._base_manager.get(slug="missing")
        self.assertEqual((missing.name, missing.type, missing.color), ("Missing", "undeployable", "dc3545"))

        normal = StatusLabel._base_manager.create(
            name="Post-restore available",
            slug="post-restore-available",
            type=StatusLabel.TYPE_DEPLOYABLE,
            color="28a745",
        )
        self.assertNotEqual(normal.pk, missing.pk)
        self.assertTrue(StatusLabel._base_manager.filter(pk=normal.pk).exists())
