"""Phase 0 regression (C5): Provider's soft-delete UniqueConstraints must carry
the active-rows-only condition (deleted_at__isnull=True).

Before the fix, re-creating a Provider with the same name/slug/tenant after a
soft-delete raised IntegrityError because the partial unique indexes still
matched the soft-deleted row.
"""
from django.test import TestCase

from organization.models import Tenant, TenantGroup
from subscriptions.models import Provider


class ProviderSoftDeleteUniqueTests(TestCase):
    def setUp(self):
        self.tg = TenantGroup.objects.create(name="Group", slug="group")
        self.tenant = Tenant.objects.create(
            name="Tenant Inc.", slug="tenant-inc", group=self.tg
        )

    def test_recreate_after_soft_delete_reuses_name_slug(self):
        original = Provider.objects.create(
            name="AWS", slug="aws", tenant=self.tenant
        )
        original.delete()  # soft delete: sets deleted_at
        self.assertIsNotNone(original.deleted_at)

        # Re-creating with the same name + slug + tenant must succeed now that the
        # soft-deleted row is excluded from the unique constraint.
        recreated = Provider.objects.create(
            name="AWS", slug="aws", tenant=self.tenant
        )
        self.assertNotEqual(recreated.pk, original.pk)
        self.assertEqual(recreated.name, "AWS")
        self.assertEqual(recreated.slug, "aws")
        self.assertEqual(recreated.tenant, self.tenant)
