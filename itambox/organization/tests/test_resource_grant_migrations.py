import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from core.tasks.resource_grants import CODE_NO_DUE, CODE_SUCCESS, sweep_expired_resource_grants
from inventory.models import AccessoryStock
from organization.models import TenantResourceGrant, TenantResourceGrantExpiryRun


@pytest.mark.serial_only
class ResourceGrantExpiryMigrationTests(TransactionTestCase):
    migrate_from = ("organization", "0102_alter_tenantresourcegrant_options")
    migrate_to = ("organization", "0103_tenant_resource_grant_expiry")

    def test_forward_and_reverse_states_are_schema_only(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        old_tenant_model = old_apps.get_model("organization", "Tenant")
        old_group_model = old_apps.get_model("organization", "TenantGroup")
        old_grant = old_apps.get_model("organization", "TenantResourceGrant")
        self.assertNotIn("valid_until", {field.name for field in old_grant._meta.fields})
        owner = old_tenant_model.objects.create(name="Migration Owner", slug="migration-owner")
        grantee = old_tenant_model.objects.create(name="Migration Grantee", slug="migration-grantee")
        group = old_group_model.objects.create(name="Migration Group", slug="migration-group")
        group_grantee = old_tenant_model.objects.create(
            name="Migration Group Grantee",
            slug="migration-group-grantee",
            group=group,
        )
        resource_type = ContentType.objects.get_for_model(AccessoryStock)
        revoked_at = timezone.now()
        direct = old_grant(
            tenant=owner,
            grantee_tenant=grantee,
            resource_type_id=resource_type.pk,
            resource_id=1001,
            access_level="view",
        )
        group_grant = old_grant(
            tenant=owner,
            grantee_tenant_group=group,
            resource_type_id=resource_type.pk,
            resource_id=1002,
            access_level="view",
        )
        revoked = old_grant(
            tenant=owner,
            grantee_tenant=grantee,
            resource_type_id=resource_type.pk,
            resource_id=1003,
            access_level="view",
            deleted_at=revoked_at,
        )
        old_grant._base_manager.bulk_create([direct, group_grant, revoked])

        # A fresh executor is required after the first migrate: the loader
        # caches the applied-migration state at construction time, so reusing
        # the instance would skip the forward migration.
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        new_apps = executor.loader.project_state([self.migrate_to]).apps
        new_grant = new_apps.get_model("organization", "TenantResourceGrant")
        run = new_apps.get_model("organization", "TenantResourceGrantExpiryRun")
        evidence = new_apps.get_model("organization", "TenantResourceGrantExpiryRevocation")
        self.assertIn("valid_until", {field.name for field in new_grant._meta.fields})
        self.assertTrue(run._meta.db_table)
        self.assertTrue(evidence._meta.db_table)
        for row in (direct, group_grant, revoked):
            current = TenantResourceGrant._base_manager.get(pk=row.pk)
            self.assertIsNone(current.valid_until)
        self.assertIsNotNone(TenantResourceGrant._base_manager.get(pk=revoked.pk).deleted_at)

        first_run = TenantResourceGrantExpiryRun._base_manager.create(
            tenant_id=owner.pk,
            schedule_slot=timezone.now().replace(minute=0, second=0, microsecond=0),
            cutoff=timezone.now(),
            dispatch_stale_at=timezone.now() + timezone.timedelta(minutes=1),
        )
        first_result = sweep_expired_resource_grants(owner.pk, first_run.pk, 1)
        self.assertEqual(first_result.code, CODE_NO_DUE)
        self.assertIsNone(TenantResourceGrant._base_manager.get(pk=direct.pk).deleted_at)
        self.assertIsNone(TenantResourceGrant._base_manager.get(pk=group_grant.pk).deleted_at)

        expired_grantee = TenantResourceGrant._base_manager.create(
            tenant_id=owner.pk,
            grantee_tenant_id=group_grantee.pk,
            resource_type_id=resource_type.pk,
            resource_id=1004,
            access_level="view",
            valid_until=timezone.now(),
        )
        cutoff = timezone.now()
        expiry_run = TenantResourceGrantExpiryRun._base_manager.create(
            tenant_id=owner.pk,
            schedule_slot=cutoff.replace(minute=0, second=0, microsecond=0) + timezone.timedelta(hours=1),
            cutoff=cutoff,
            dispatch_stale_at=cutoff + timezone.timedelta(minutes=1),
        )
        result = sweep_expired_resource_grants(owner.pk, expiry_run.pk, 1)
        self.assertEqual(result.code, CODE_SUCCESS)
        expired_grantee.refresh_from_db()
        self.assertIsNotNone(expired_grantee.deleted_at)

        executor.migrate([self.migrate_from])
        reversed_apps = executor.loader.project_state([self.migrate_from]).apps
        reversed_grant = reversed_apps.get_model("organization", "TenantResourceGrant")
        self.assertNotIn("valid_until", {field.name for field in reversed_grant._meta.fields})
        for row in (direct, group_grant, revoked, expired_grantee):
            historical = reversed_grant._base_manager.get(pk=row.pk)
            self.assertEqual(historical.tenant_id, owner.pk)
        self.assertIsNotNone(reversed_grant._base_manager.get(pk=revoked.pk).deleted_at)
        self.assertIsNotNone(reversed_grant._base_manager.get(pk=expired_grantee.pk).deleted_at)
        MigrationExecutor(connection).migrate([self.migrate_to])
