"""Phase 0 regression (C5): AssetTagSequence's soft-delete UniqueConstraints must
carry the active-rows-only condition (deleted_at__isnull=True).

Before the fix, re-creating an AssetTagSequence with the same prefix after a
soft-delete raised IntegrityError because the partial unique indexes still
matched the soft-deleted row.
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from assets.models import AssetTagSequence, Category
from organization.models import Tenant, TenantGroup


class AssetTagSequenceSoftDeleteUniqueTests(TestCase):
    def setUp(self):
        self.tg = TenantGroup.objects.create(name="Group", slug="group")
        self.tenant = Tenant.objects.create(name="Tenant Inc.", slug="tenant-inc", group=self.tg)

    def test_recreate_after_soft_delete_reuses_prefix(self):
        original = AssetTagSequence.objects.create(prefix="ASSET-", tenant=self.tenant)
        # Exercise the locking path before deletion.
        original.next_tag()

        original.delete()  # soft delete: sets deleted_at
        self.assertIsNotNone(original.deleted_at)

        # Re-creating with the same prefix + tenant must succeed now that the
        # soft-deleted row is excluded from the unique constraint.
        recreated = AssetTagSequence.objects.create(prefix="ASSET-", tenant=self.tenant)
        self.assertNotEqual(recreated.pk, original.pk)
        self.assertEqual(recreated.prefix, "ASSET-")
        self.assertEqual(recreated.tenant, self.tenant)

        # And the new sequence can also generate tags.
        tag = recreated.next_tag()
        self.assertTrue(tag.startswith("ASSET-"))


class AssetTagSequenceArchivedResolutionTests(TestCase):
    def setUp(self):
        group = TenantGroup.objects.create(name="Archive Group", slug="archive-group")
        self.tenant = Tenant.objects.create(name="Archive Tenant", slug="archive-tenant", group=group)
        self.asset = SimpleNamespace(tenant_id=self.tenant.pk, category=None)

    def test_archived_tenant_sequence_is_not_used_for_preview(self):
        archived = AssetTagSequence.all_objects.create(tenant=self.tenant, prefix="OLD-", next_value=9, zero_padding=4)
        archived.delete()
        replacement = AssetTagSequence.objects.create(tenant=self.tenant, prefix="NEW-", zero_padding=4)

        resolved = AssetTagSequence.resolve_sequence_for_asset(self.asset)

        self.assertEqual(resolved.pk, replacement.pk)
        self.assertEqual(resolved.next_tag_preview, "NEW-0001")

    def test_archived_tenant_sequence_is_not_used_for_tag_generation(self):
        archived = AssetTagSequence.all_objects.create(tenant=self.tenant, prefix="OLD-", next_value=9, zero_padding=4)
        archived.delete()
        replacement = AssetTagSequence.objects.create(tenant=self.tenant, prefix="NEW-", zero_padding=4)

        tag = AssetTagSequence.get_next_tag_for_asset(self.asset)

        self.assertEqual(tag, "NEW-0001")
        archived.refresh_from_db()
        replacement.refresh_from_db()
        self.assertEqual(archived.next_value, 9)
        self.assertEqual(replacement.next_value, 2)

    def test_archived_global_default_is_replaced_for_preview_and_generation(self):
        archived = AssetTagSequence.all_objects.create(prefix="ASSET-", next_value=12, zero_padding=4)
        archived.delete()
        global_asset = SimpleNamespace(tenant_id=None, category=None)

        resolved = AssetTagSequence.resolve_sequence_for_asset(global_asset)
        tag = AssetTagSequence.get_next_tag_for_asset(global_asset)

        self.assertNotEqual(resolved.pk, archived.pk)
        self.assertIsNone(resolved.deleted_at)
        self.assertEqual(resolved.next_tag_preview, "ASSET-000001")
        self.assertEqual(tag, "ASSET-000001")
        archived.refresh_from_db()
        self.assertEqual(archived.next_value, 12)

    def test_historical_global_default_does_not_make_live_default_ambiguous(self):
        archived = AssetTagSequence.all_objects.create(prefix="ASSET-", next_value=12, zero_padding=4)
        archived.delete()
        replacement = AssetTagSequence.all_objects.create(prefix="ASSET-", next_value=20, zero_padding=4)
        global_asset = SimpleNamespace(tenant_id=None, category=None)

        resolved = AssetTagSequence.resolve_sequence_for_asset(global_asset)
        tag = AssetTagSequence.get_next_tag_for_asset(global_asset)

        self.assertEqual(resolved.pk, replacement.pk)
        self.assertEqual(resolved.next_tag_preview, "ASSET-0020")
        self.assertEqual(tag, "ASSET-0020")
        archived.refresh_from_db()
        self.assertEqual(archived.next_value, 12)

    def test_archived_category_sequences_are_not_used_for_preview_or_generation(self):
        category = Category.objects.create(name="Archived Category")
        archived_tenant = AssetTagSequence.all_objects.create(
            tenant=self.tenant, category=category, prefix="OLD-T-", zero_padding=4
        )
        archived_global = AssetTagSequence.all_objects.create(category=category, prefix="OLD-G-", zero_padding=4)
        archived_tenant.delete()
        archived_global.delete()
        tenant_replacement = AssetTagSequence.objects.create(
            tenant=self.tenant, category=category, prefix="NEW-T-", zero_padding=4
        )
        global_replacement = AssetTagSequence.objects.create(category=category, prefix="NEW-G-", zero_padding=4)
        tenant_asset = SimpleNamespace(tenant_id=self.tenant.pk, category=category)
        global_asset = SimpleNamespace(tenant_id=None, category=category)

        self.assertEqual(AssetTagSequence.resolve_sequence_for_asset(tenant_asset).pk, tenant_replacement.pk)
        self.assertEqual(AssetTagSequence.get_next_tag_for_asset(tenant_asset), "NEW-T-0001")
        self.assertEqual(AssetTagSequence.resolve_sequence_for_asset(global_asset).pk, global_replacement.pk)
        self.assertEqual(AssetTagSequence.get_next_tag_for_asset(global_asset), "NEW-G-0001")
        archived_tenant.refresh_from_db()
        archived_global.refresh_from_db()
        self.assertEqual(archived_tenant.next_value, 1)
        self.assertEqual(archived_global.next_value, 1)

    def test_tag_generation_bounds_retries_when_every_selected_sequence_is_archived(self):
        with patch.object(AssetTagSequence, "_allocate_next_tag_for_asset", side_effect=AssetTagSequence.DoesNotExist):
            with self.assertRaises(AssetTagSequence.DoesNotExist):
                AssetTagSequence.get_next_tag_for_asset(self.asset)
