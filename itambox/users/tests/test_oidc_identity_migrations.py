import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


@pytest.mark.serial_only
class OIDCIdentityMigrationTests(TransactionTestCase):
    migrate_from = ("users", "0102_token_updated_at")
    migrate_to = ("users", "0103_oidcidentity")

    def test_forward_and_reverse_preserve_predecessor_users_without_backfill(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        User = old_apps.get_model("users", "User")
        predecessor_user = User.objects.create(username="migration-predecessor")
        predecessor_user_pk = predecessor_user.pk

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        new_apps = executor.loader.project_state([self.migrate_to]).apps
        User = new_apps.get_model("users", "User")
        OIDCIdentity = new_apps.get_model("users", "OIDCIdentity")
        table_name = OIDCIdentity._meta.db_table

        self.assertEqual(OIDCIdentity.objects.count(), 0)
        self.assertTrue(User.objects.filter(pk=predecessor_user_pk).exists())
        self.assertEqual(OIDCIdentity._meta.get_field("issuer").max_length, 2000)
        self.assertEqual(OIDCIdentity._meta.get_field("subject").max_length, 255)

        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, table_name)
        unique_pairs = [
            constraint
            for constraint in constraints.values()
            if constraint["unique"] and constraint["columns"] == ["issuer", "subject"]
        ]
        self.assertEqual(len(unique_pairs), 1)
        self.assertEqual(unique_pairs[0]["columns"], ["issuer", "subject"])
        self.assertIn("users_oidcidentity_unique_issuer_subject", constraints)

        binding = OIDCIdentity.objects.create(
            user_id=predecessor_user_pk,
            issuer="https://migration.example/issuer",
            subject="migration-subject",
        )
        self.assertEqual(binding.user_id, predecessor_user_pk)

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        reversed_apps = executor.loader.project_state([self.migrate_from]).apps
        User = reversed_apps.get_model("users", "User")
        self.assertTrue(User.objects.filter(pk=predecessor_user_pk).exists())
        self.assertNotIn(table_name, connection.introspection.table_names())

        MigrationExecutor(connection).migrate([self.migrate_to])
