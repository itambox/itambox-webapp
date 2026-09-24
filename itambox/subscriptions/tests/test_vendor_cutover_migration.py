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
        JournalEntry = old_apps.get_model("extras", "JournalEntry")
        FileAttachment = old_apps.get_model("extras", "FileAttachment")
        Bookmark = old_apps.get_model("extras", "Bookmark")
        Event = old_apps.get_model("extras", "Event")
        Contact = old_apps.get_model("organization", "Contact")
        ContactRole = old_apps.get_model("organization", "ContactRole")
        ContactAssignment = old_apps.get_model("organization", "ContactAssignment")
        Tenant = old_apps.get_model("organization", "Tenant")
        Role = old_apps.get_model("organization", "Role")
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

        # Per-object references that must follow the merge.
        cutover_tenant = Tenant.objects.create(name=f"Cutover tenant {suffix}", slug=f"cutover-tenant-{suffix}")
        journaled_provider = Provider.objects.create(
            name=f"Journaled vendor {suffix}",
            slug=f"journaled-vendor-{suffix}",
            tenant=cutover_tenant,
        )
        journal_entry = JournalEntry.objects.create(
            model=provider_ct,
            object_id=journaled_provider.pk,
            comment="Provider journal comment",
            tenant=None,  # stale: the cutover must re-derive it from the supplier
        )
        attachment = FileAttachment.objects.create(
            model=provider_ct,
            object_id=matched_provider.pk,
            file=f"attachments/files/{suffix}.txt",
            name="Provider file",
        )
        event = Event.objects.create(model=provider_ct, object_id=matched_provider.pk, action="create")
        # The users state pinned by this rehearsal predates later user columns
        # (e.g. scim_id), so the historical User model cannot create a row here;
        # insert the minimal bookmark owner directly.
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users_user "
                "(username, scim_id, password, is_superuser, first_name, last_name, email, "
                "is_staff, is_active, can_login, date_joined) "
                "VALUES (%s, %s, '', false, '', '', '', false, true, true, NOW()) "
                "RETURNING id",
                [f"cutover-bookmark-{suffix}", str(uuid.uuid4())],
            )
            bookmark_user_id = cursor.fetchone()[0]
        Bookmark.objects.create(user_id=bookmark_user_id, model=supplier_ct, object_id=prelinked.pk)
        Bookmark.objects.create(user_id=bookmark_user_id, model=provider_ct, object_id=linked_provider.pk)

        role_a = Role.objects.create(
            tenant=cutover_tenant,
            name=f"Legacy provider role A {suffix}",
            slug=f"legacy-provider-role-a-{suffix}",
            permissions=[
                "subscriptions.view_provider",
                "subscriptions.delete_provider",
                "assets.view_asset",
            ],
        )
        role_b = Role.objects.create(
            tenant=cutover_tenant,
            name=f"Legacy provider role B {suffix}",
            slug=f"legacy-provider-role-b-{suffix}",
            permissions=["subscriptions.add_provider", "assets.add_supplier"],
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
            "journal_entry_id": journal_entry.pk,
            "journaled_provider_id": journaled_provider.pk,
            "attachment_id": attachment.pk,
            "event_id": event.pk,
            "bookmark_user_id": bookmark_user_id,
            "role_a_id": role_a.pk,
            "role_b_id": role_b.pk,
            "cutover_tenant_id": cutover_tenant.pk,
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
        JournalEntry = self.apps.get_model("extras", "JournalEntry")
        FileAttachment = self.apps.get_model("extras", "FileAttachment")
        Bookmark = self.apps.get_model("extras", "Bookmark")
        Event = self.apps.get_model("extras", "Event")
        Role = self.apps.get_model("organization", "Role")

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

        journaled_supplier = Supplier.objects.get(name=f"Journaled vendor {expected['suffix']}")
        journal = JournalEntry.objects.get(pk=expected["journal_entry_id"])
        self.assertEqual(journal.model_id, expected["supplier_ct_id"])
        self.assertEqual(journal.object_id, journaled_supplier.pk)
        self.assertEqual(journal.tenant_id, expected["cutover_tenant_id"])

        attachment = FileAttachment.objects.get(pk=expected["attachment_id"])
        self.assertEqual(attachment.model_id, expected["supplier_ct_id"])
        self.assertEqual(attachment.object_id, expected["matched_supplier"])

        event = Event.objects.get(pk=expected["event_id"])
        self.assertEqual(event.model_id, expected["supplier_ct_id"])
        self.assertEqual(event.object_id, expected["matched_supplier"])

        # The provider-era bookmark duplicates the pre-existing supplier bookmark
        # and must be dropped, leaving exactly the supplier-owned row.
        bookmarks = Bookmark.objects.filter(user_id=expected["bookmark_user_id"])
        self.assertEqual(bookmarks.count(), 1)
        surviving_bookmark = bookmarks.get()
        self.assertEqual(surviving_bookmark.model_id, expected["supplier_ct_id"])
        self.assertEqual(surviving_bookmark.object_id, expected["linked_supplier"])

        # Custom roles keep working across the rename: legacy provider grants are
        # translated in place (order preserved, duplicates deduped) instead of
        # silently locking their holders out of the assets.view_supplier gate.
        role_a = Role.objects.get(pk=expected["role_a_id"])
        role_b = Role.objects.get(pk=expected["role_b_id"])
        self.assertEqual(
            role_a.permissions,
            ["assets.view_supplier", "assets.delete_supplier", "assets.view_asset"],
        )
        self.assertEqual(role_b.permissions, ["assets.add_supplier"])

        with self.assertRaises(LookupError):
            self.apps.get_model("subscriptions", "Provider")
