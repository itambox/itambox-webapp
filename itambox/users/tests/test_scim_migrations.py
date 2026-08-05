import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


@pytest.mark.serial_only
class SCIMIdentityMigrationTests(TransactionTestCase):
    migrate_from = [
        ("organization", "0100_issue88_shard_61_organization_relations"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]
    migrate_to = [
        ("organization", "0101_membership_external_id_and_more"),
        ("users", "0101_user_scim_id_usergroup_external_id_usergroup_scim_id_and_more"),
    ]

    def test_forward_populates_ids_and_reverse_preserves_existing_rows(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        Tenant = old_apps.get_model("organization", "Tenant")
        User = old_apps.get_model("users", "User")
        Membership = old_apps.get_model("organization", "Membership")
        UserGroup = old_apps.get_model("users", "UserGroup")
        GroupMembership = old_apps.get_model("users", "GroupMembership")

        tenant = Tenant.objects.create(name="Migration Tenant", slug="migration-tenant")
        user = User.objects.create(username="migration-user")
        extra_user = User.objects.create(username="migration-extra-user")
        membership = Membership.objects.create(user=user, tenant=tenant, is_active=True)
        extra_membership = Membership.objects.create(user=extra_user, tenant=tenant, is_active=True)
        group = UserGroup.objects.create(tenant=tenant, name="Migration Group")
        extra_group = UserGroup.objects.create(tenant=tenant, name="Migration Extra Group")
        deleted_group = UserGroup.objects.create(tenant=tenant, name="Migration Deleted Group")
        deleted_group.deleted_at = timezone.now()
        deleted_group.save(update_fields=["deleted_at"])
        group_membership = GroupMembership.objects.create(
            user_group=group,
            membership=membership,
            source="scim",
            external_id="legacy-provenance",
        )
        extra_group_membership = GroupMembership.objects.create(
            user_group=extra_group,
            membership=extra_membership,
            source="manual",
            external_id="legacy-extra-provenance",
        )
        user_pks = [user.pk, extra_user.pk]
        group_pks = [group.pk, extra_group.pk, deleted_group.pk]
        user_pk, group_pk, membership_pk, group_membership_pk = (
            user.pk,
            group.pk,
            membership.pk,
            group_membership.pk,
        )
        extra_membership_pk, extra_group_membership_pk = extra_membership.pk, extra_group_membership.pk

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        User = new_apps.get_model("users", "User")
        UserGroup = new_apps.get_model("users", "UserGroup")
        Membership = new_apps.get_model("organization", "Membership")

        migrated_user = User.objects.get(pk=user_pk)
        migrated_group = UserGroup.objects.get(pk=group_pk)
        migrated_membership = Membership.objects.get(pk=membership_pk)
        self.assertIsNotNone(migrated_user.scim_id)
        self.assertIsNotNone(migrated_group.scim_id)
        self.assertEqual(migrated_membership.external_id, "")
        migrated_user_ids = list(User.objects.filter(pk__in=user_pks).values_list("scim_id", flat=True))
        migrated_group_ids = list(UserGroup.objects.filter(pk__in=group_pks).values_list("scim_id", flat=True))
        self.assertEqual(len(migrated_user_ids), len(set(migrated_user_ids)))
        self.assertEqual(len(migrated_group_ids), len(set(migrated_group_ids)))
        self.assertEqual(User.objects.filter(scim_id=migrated_user.scim_id).count(), 1)
        self.assertEqual(UserGroup.objects.filter(scim_id=migrated_group.scim_id).count(), 1)
        self.assertTrue(UserGroup.objects.filter(pk=group_pks[-1], deleted_at__isnull=False).exists())

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        reversed_apps = executor.loader.project_state(self.migrate_from).apps
        User = reversed_apps.get_model("users", "User")
        UserGroup = reversed_apps.get_model("users", "UserGroup")
        Membership = reversed_apps.get_model("organization", "Membership")
        GroupMembership = reversed_apps.get_model("users", "GroupMembership")

        self.assertTrue(User.objects.filter(pk=user_pk, username="migration-user").exists())
        self.assertTrue(UserGroup.objects.filter(pk=group_pk, name="Migration Group").exists())
        self.assertTrue(Membership.objects.filter(pk=membership_pk, user_id=user_pk).exists())
        self.assertTrue(Membership.objects.filter(pk=extra_membership_pk, user_id=user_pks[-1]).exists())
        self.assertTrue(GroupMembership.objects.filter(pk=group_membership_pk).exists())
        self.assertTrue(GroupMembership.objects.filter(pk=extra_group_membership_pk).exists())

        MigrationExecutor(connection).migrate(self.migrate_to)
