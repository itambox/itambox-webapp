"""Phase 0 regression (C5): AssetTagSequence's soft-delete UniqueConstraints must
carry the active-rows-only condition (deleted_at__isnull=True).

Before the fix, re-creating an AssetTagSequence with the same prefix after a
soft-delete raised IntegrityError because the partial unique indexes still
matched the soft-deleted row.
"""
from django.test import TestCase

from assets.models import AssetTagSequence
from organization.models import Tenant, TenantGroup


class AssetTagSequenceSoftDeleteUniqueTests(TestCase):
    def setUp(self):
        self.tg = TenantGroup.objects.create(name="Group", slug="group")
        self.tenant = Tenant.objects.create(
            name="Tenant Inc.", slug="tenant-inc", group=self.tg
        )

    def test_recreate_after_soft_delete_reuses_prefix(self):
        original = AssetTagSequence.objects.create(
            prefix="ASSET-", tenant=self.tenant
        )
        # Exercise the locking path before deletion.
        original.next_tag()

        original.delete()  # soft delete: sets deleted_at
        self.assertIsNotNone(original.deleted_at)

        # Re-creating with the same prefix + tenant must succeed now that the
        # soft-deleted row is excluded from the unique constraint.
        recreated = AssetTagSequence.objects.create(
            prefix="ASSET-", tenant=self.tenant
        )
        self.assertNotEqual(recreated.pk, original.pk)
        self.assertEqual(recreated.prefix, "ASSET-")
        self.assertEqual(recreated.tenant, self.tenant)

        # And the new sequence can also generate tags.
        tag = recreated.next_tag()
        self.assertTrue(tag.startswith("ASSET-"))
