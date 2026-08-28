import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


@pytest.mark.serial_only
class CanonicalMissingStatusMigrationTests(TransactionTestCase):
    migrate_from = ("assets", "0100_issue88_shard_43_assets_seed")
    migrate_to = ("assets", "0101_seed_canonical_missing_status")

    def test_forward_reverse_forward_is_additive_and_idempotent(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        StatusLabel = old_apps.get_model("assets", "StatusLabel")
        StatusLabel.objects.filter(slug="missing").delete()
        self.assertFalse(StatusLabel.objects.filter(slug="missing").exists())

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        migrated_apps = executor.loader.project_state([self.migrate_to]).apps
        StatusLabel = migrated_apps.get_model("assets", "StatusLabel")
        missing = StatusLabel.objects.get(slug="missing")
        self.assertEqual((missing.name, missing.type, missing.color), ("Missing", "undeployable", "dc3545"))

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        reversed_apps = executor.loader.project_state([self.migrate_from]).apps
        StatusLabel = reversed_apps.get_model("assets", "StatusLabel")
        self.assertEqual(StatusLabel.objects.filter(slug="missing").count(), 1)

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        reapplied_apps = executor.loader.project_state([self.migrate_to]).apps
        StatusLabel = reapplied_apps.get_model("assets", "StatusLabel")
        self.assertEqual(StatusLabel.objects.filter(slug="missing").count(), 1)
