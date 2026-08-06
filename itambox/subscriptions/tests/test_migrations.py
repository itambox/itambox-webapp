import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


@pytest.mark.serial_only
class SubscriptionVocabularyMigrationTests(TransactionTestCase):
    migrate_from = ("subscriptions", "0100_issue88_shard_47_subscriptions_relations")
    migrate_to = ("subscriptions", "0101_remove_subscription_auto_renewal_and_more")

    def test_legacy_statuses_and_auto_renewal_values_are_preserved(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        Provider = old_apps.get_model("subscriptions", "Provider")
        Subscription = old_apps.get_model("subscriptions", "Subscription")

        provider = Provider.objects.create(name="Migration Provider")
        rows = [
            Subscription.objects.create(
                name=f"Legacy {status}",
                provider=provider,
                status=status,
                auto_renewal=auto_renewal,
            )
            for status, auto_renewal in (("pending", False), ("trial", True), ("renewing", False))
        ]

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        new_apps = executor.loader.project_state([self.migrate_to]).apps
        Subscription = new_apps.get_model("subscriptions", "Subscription")

        migrated = [Subscription.objects.get(pk=row.pk) for row in rows]
        self.assertEqual([row.status for row in migrated], ["active", "active", "active"])
        self.assertEqual(
            [row.vendor_contract_auto_renews for row in migrated],
            [False, True, False],
        )

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        reversed_apps = executor.loader.project_state([self.migrate_from]).apps
        Subscription = reversed_apps.get_model("subscriptions", "Subscription")
        reversed_rows = [Subscription.objects.get(pk=row.pk) for row in rows]
        self.assertEqual([row.status for row in reversed_rows], ["active", "active", "active"])
        self.assertEqual([row.auto_renewal for row in reversed_rows], [False, True, False])

        MigrationExecutor(connection).migrate([self.migrate_to])
