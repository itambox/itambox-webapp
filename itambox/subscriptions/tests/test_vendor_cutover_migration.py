import uuid

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.graph import MigrationGraph
from django.test import TransactionTestCase
from django.utils import timezone

MIGRATE_FROM = ("subscriptions", "0102_commercial_vendor_and_terms")
ASSET_STATE = ("assets", "0121_supplier_scoping_and_commercial_fields")
MIGRATE_TO = ("subscriptions", "0103_unified_vendor_cutover")


@pytest.mark.serial_only
class UnifiedVendorCutoverMigrationTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(self._restore_leaf)
        self.executor = self._scoped_executor()
        self.executor.migrate([MIGRATE_FROM, ASSET_STATE])
        old_apps = self.executor.loader.project_state([MIGRATE_FROM, ASSET_STATE]).apps

        Provider = old_apps.get_model("subscriptions", "Provider")
        Subscription = old_apps.get_model("subscriptions", "Subscription")
        Supplier = old_apps.get_model("assets", "Supplier")
        Tag = old_apps.get_model("extras", "Tag")
        Contact = old_apps.get_model("organization", "Contact")
        ContactRole = old_apps.get_model("organization", "ContactRole")
        ContactAssignment = old_apps.get_model("organization", "ContactAssignment")
        ContentType = old_apps.get_model("contenttypes", "ContentType")

        suffix = uuid.uuid4().hex[:8]
        deleted_at = timezone.now()
        existing = Supplier.objects.create(
            name=f"Acme Cloud {suffix}",
            slug=f"acme-cloud-{suffix}",
            notes="Keep these supplier notes",
            is_active=False,
        )
        prelinked = Supplier.objects.create(name=f"Prelinked {suffix}", slug=f"prelinked-{suffix}")
        archived = Supplier.objects.create(
            name=f"Archived Vendor {suffix}",
            slug=f"archived-vendor-{suffix}",
            deleted_at=deleted_at,
        )
        slug_owner = Supplier.objects.create(name=f"Slug Owner {suffix}", slug=f"legacy-slug-{suffix}")

        linked_provider = Provider.objects.create(
            name=f"Different legacy name {suffix}",
            slug=f"linked-provider-{suffix}",
            supplier=prelinked,
            portal_url=f"https://portal.example/{suffix}",
            account_id=f"linked-{suffix}",
            admin_notes="Must not replace matched supplier notes",
        )
        matched_provider = Provider.objects.create(
            name=f"ACME CLOUD {suffix}",
            slug=f"matched-provider-{suffix}",
            portal_url=f"https://acme.example/{suffix}",
            account_id=f"acme-{suffix}",
            admin_notes="Must not replace matched supplier notes",
        )
        matched_provider_second = Provider.objects.create(
            name=f"acme cloud {suffix}",
            slug=f"matched-provider-second-{suffix}",
            portal_url=f"https://second-acme.example/{suffix}",
            account_id=f"second-acme-{suffix}",
        )
        deleted_provider = Provider.objects.create(
            name=f"Archived Vendor {suffix}",
            slug=f"deleted-archive-{suffix}",
            deleted_at=deleted_at,
        )
        live_provider = Provider.objects.create(
            name=f"Archived Vendor {suffix}",
            slug=f"live-archive-{suffix}",
        )
        collision_provider = Provider.objects.create(
            name=f"Fresh Vendor {suffix}",
            slug=f"legacy-slug-{suffix}",
            admin_notes="Copied notes",
        )

        hidden_at = timezone.now()
        provider_subscriptions = {}
        for label, provider in (
            ("linked", linked_provider),
            ("matched", matched_provider),
            ("matched_second", matched_provider_second),
            ("deleted", deleted_provider),
            ("live", live_provider),
            ("collision", collision_provider),
        ):
            row = Subscription.objects.create(
                name=f"{label} subscription {suffix}",
                provider=provider,
            )
            provider_subscriptions[label] = row.pk
        hidden_subscription = Subscription.objects.create(
            name=f"Hidden subscription {suffix}",
            provider=matched_provider,
            deleted_at=hidden_at,
        )
        provider_subscriptions["hidden"] = hidden_subscription.pk

        tag = Tag.objects.create(name=f"Cutover tag {suffix}", slug=f"cutover-tag-{suffix}")
        matched_provider.tags.add(tag)
        existing.tags.add(tag)

        provider_ct, _ = ContentType.objects.get_or_create(app_label="subscriptions", model="provider")
        supplier_ct, _ = ContentType.objects.get_or_create(app_label="assets", model="supplier")
        role = ContactRole.objects.create(name=f"Cutover role {suffix}", slug=f"cutover-role-{suffix}")
        duplicate_contact = Contact.objects.create(name=f"Duplicate contact {suffix}")
        ContactAssignment.objects.create(
            contact=duplicate_contact,
            role=role,
            content_type=supplier_ct,
            object_id=prelinked.pk,
        )
        ContactAssignment.objects.create(
            contact=duplicate_contact,
            role=role,
            content_type=provider_ct,
            object_id=linked_provider.pk,
        )
        moved_contact = Contact.objects.create(name=f"Moved contact {suffix}")
        ContactAssignment.objects.create(
            contact=moved_contact,
            role=role,
            content_type=provider_ct,
            object_id=matched_provider.pk,
        )

        self.expected = {
            "linked_supplier": prelinked.pk,
            "matched_supplier": existing.pk,
            "deleted_supplier": archived.pk,
            "provider_subscriptions": provider_subscriptions,
            "hidden_at": hidden_at,
            "deleted_at": deleted_at,
            "tag_id": tag.pk,
            "provider_ct_id": provider_ct.pk,
            "supplier_ct_id": supplier_ct.pk,
            "duplicate_contact_id": duplicate_contact.pk,
            "moved_contact_id": moved_contact.pk,
            "role_id": role.pk,
            "slug_owner_id": slug_owner.pk,
            "suffix": suffix,
        }

        connection.commit()
        connection.close()
        self.executor = self._scoped_executor()
        self.executor.migrate([MIGRATE_TO, ASSET_STATE])
        self.apps = self.executor.loader.project_state([MIGRATE_TO, ASSET_STATE]).apps

    @staticmethod
    def _scoped_executor():
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

    @staticmethod
    def _restore_leaf():
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_cutover_maps_vendors_and_preserves_related_data(self):
        Subscription = self.apps.get_model("subscriptions", "Subscription")
        Supplier = self.apps.get_model("assets", "Supplier")
        ContactAssignment = self.apps.get_model("organization", "ContactAssignment")

        expected = self.expected
        subscriptions = {
            label: Subscription.objects.get(pk=pk) for label, pk in expected["provider_subscriptions"].items()
        }
        self.assertEqual(subscriptions["linked"].supplier_id, expected["linked_supplier"])
        self.assertEqual(subscriptions["matched"].supplier_id, expected["matched_supplier"])
        self.assertEqual(subscriptions["matched_second"].supplier_id, expected["matched_supplier"])
        self.assertEqual(subscriptions["deleted"].supplier_id, expected["deleted_supplier"])
        self.assertEqual(subscriptions["hidden"].supplier_id, expected["matched_supplier"])
        self.assertEqual(subscriptions["hidden"].deleted_at, expected["hidden_at"])

        live_archive = Supplier.objects.get(name=f"Archived Vendor {expected['suffix']}", deleted_at__isnull=True)
        self.assertEqual(subscriptions["live"].supplier_id, live_archive.pk)
        fresh_supplier = Supplier.objects.get(name=f"Fresh Vendor {expected['suffix']}")
        self.assertEqual(subscriptions["collision"].supplier_id, fresh_supplier.pk)
        self.assertEqual(fresh_supplier.slug, f"fresh-vendor-{expected['suffix']}")
        self.assertEqual(fresh_supplier.notes, "Copied notes")
        self.assertEqual(
            Supplier.objects.get(pk=expected["slug_owner_id"]).slug,
            f"legacy-slug-{expected['suffix']}",
        )

        matched_supplier = Supplier.objects.get(pk=expected["matched_supplier"])
        self.assertEqual(matched_supplier.name, f"Acme Cloud {expected['suffix']}")
        self.assertEqual(matched_supplier.notes, "Keep these supplier notes")
        self.assertFalse(matched_supplier.is_active)
        self.assertEqual(matched_supplier.portal_url, f"https://acme.example/{expected['suffix']}")
        self.assertEqual(matched_supplier.account_id, f"acme-{expected['suffix']}")
        self.assertEqual(list(matched_supplier.tags.values_list("pk", flat=True)), [expected["tag_id"]])

        deleted_supplier = Supplier.objects.get(pk=expected["deleted_supplier"])
        self.assertEqual(deleted_supplier.deleted_at, expected["deleted_at"])
        self.assertEqual(
            Supplier.objects.get(pk=expected["linked_supplier"]).portal_url,
            f"https://portal.example/{expected['suffix']}",
        )

        assignments = ContactAssignment.objects.filter(
            contact_id=expected["duplicate_contact_id"],
            role_id=expected["role_id"],
            content_type_id=expected["supplier_ct_id"],
            object_id=expected["linked_supplier"],
        )
        self.assertEqual(assignments.count(), 1)
        self.assertFalse(
            ContactAssignment.objects.filter(
                contact_id=expected["duplicate_contact_id"],
                role_id=expected["role_id"],
                content_type_id=expected["provider_ct_id"],
            ).exists()
        )
        self.assertTrue(
            ContactAssignment.objects.filter(
                contact_id=expected["moved_contact_id"],
                role_id=expected["role_id"],
                content_type_id=expected["supplier_ct_id"],
                object_id=expected["matched_supplier"],
            ).exists()
        )

        with self.assertRaises(LookupError):
            self.apps.get_model("subscriptions", "Provider")
