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
        TenantGroup = old_apps.get_model("organization", "TenantGroup")
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
        journal_tenant_group = TenantGroup.objects.create(
            name=f"Journal group {suffix}", slug=f"journal-group-{suffix}"
        )
        group_journaled_provider = Provider.objects.create(
            name=f"Group journaled vendor {suffix}",
            slug=f"group-journaled-vendor-{suffix}",
            tenant_group=journal_tenant_group,
        )
        group_journal_entry = JournalEntry.objects.create(
            model=provider_ct,
            object_id=group_journaled_provider.pk,
            comment="Group provider journal comment",
            tenant=None,  # stale: the cutover must re-derive it from the supplier
        )
        attachment = FileAttachment.objects.create(
            model=provider_ct,
            object_id=matched_provider.pk,
            file=f"attachments/files/{suffix}.txt",
            name="Provider file",
        )
        event = Event.objects.create(
            model=provider_ct,
            object_id=matched_provider.pk,
            action="create",
            data={"app_label": "subscriptions", "model_name": "provider"},
        )
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

        # Two tenant-scoped providers deliberately linked to the same existing
        # (global) supplier: the cutover must keep their commercial data in
        # scoped incarnations instead of collapsing both into the shared row.
        second_tenant = Tenant.objects.create(
            name=f"Second cutover tenant {suffix}", slug=f"second-cutover-tenant-{suffix}"
        )
        shared_link_provider_a = Provider.objects.create(
            name=f"Shared link vendor {suffix}",
            slug=f"shared-link-a-{suffix}",
            tenant=cutover_tenant,
            supplier=prelinked,
            portal_url=f"https://tenant-a.example/{suffix}",
            account_id=f"tenant-a-{suffix}",
        )
        shared_link_provider_b = Provider.objects.create(
            name=f"Shared link vendor {suffix}",
            slug=f"shared-link-b-{suffix}",
            tenant=second_tenant,
            supplier=prelinked,
            portal_url=f"https://tenant-b.example/{suffix}",
            account_id=f"tenant-b-{suffix}",
        )
        shared_link_subscriptions = {}
        for link_label, link_provider in (("a", shared_link_provider_a), ("b", shared_link_provider_b)):
            row = Subscription.objects.create(
                name=f"Shared link {link_label} subscription {suffix}", provider=link_provider
            )
            shared_link_subscriptions[link_label] = row.pk
        shared_contact_a = Contact.objects.create(name=f"Shared link contact A {suffix}")
        shared_contact_b = Contact.objects.create(name=f"Shared link contact B {suffix}")
        ContactAssignment.objects.create(
            contact=shared_contact_a,
            role=role,
            content_type=provider_ct,
            object_id=shared_link_provider_a.pk,
        )
        ContactAssignment.objects.create(
            contact=shared_contact_b,
            role=role,
            content_type=provider_ct,
            object_id=shared_link_provider_b.pk,
        )

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

        # Durable Provider-bound configuration that must follow the cutover:
        # content-type bindings, report columns and saved filter parameters.
        EventRule = old_apps.get_model("extras", "EventRule")
        ExportTemplate = old_apps.get_model("extras", "ExportTemplate")
        SavedFilter = old_apps.get_model("extras", "SavedFilter")
        ReportTemplate = old_apps.get_model("extras", "ReportTemplate")
        provider_content_type = ContentType.objects.get(app_label="subscriptions", model="provider")
        event_rule = EventRule.objects.create(
            name=f"Provider change events {suffix}",
            model=provider_content_type,
            events=["create"],
            action_type="notification",
        )
        export_template = ExportTemplate.objects.create(
            name=f"Provider export {suffix}",
            content_type=provider_content_type,
            template_code=(
                "{{ queryset.first().admin_notes }}|{{ queryset.first().supplier.name }}|"
                "{{ queryset.first()['admin_notes'] }}|"
                "{{ queryset.first().supplier.subscriptions.first().supplier.name }}"
            ),
        )
        saved_filter = SavedFilter.objects.create(
            name=f"Provider subscription filter {suffix}",
            content_type=provider_content_type,
            parameters={"provider": str(linked_provider.pk)},
        )
        report_template = ReportTemplate.objects.create(
            name=f"Provider renewal report {suffix}",
            report_type="subscription_renewals",
            included_columns=["subscription_name", "provider", "cost"],
            group_by_field="provider",
            template_content="<p>{{ row['Provider'] }} / {{ row['Anbieter'] }}</p>",
        )
        unrelated_report_template = ReportTemplate.objects.create(
            name=f"Asset report {suffix}",
            report_type="asset_default",
            included_columns=["name"],
            group_by_field="name",
            template_content="<p>{{ row['Provider'] }}</p>",
        )

        # Name collisions between Provider- and Supplier-bound rows are legal
        # before the cutover; the move must stay deterministic. Unrelated saved
        # filters may contain a "provider" parameter as arbitrary data and must
        # stay untouched.
        supplier_export_template = ExportTemplate.objects.create(
            name=f"Collision export {suffix}",
            content_type=supplier_ct,
            template_code="existing",
        )
        provider_export_template = ExportTemplate.objects.create(
            name=f"Collision export {suffix}",
            content_type=provider_content_type,
            template_code="colliding",
        )
        long_name = f"Long {'x' * 250}"
        supplier_long_template = ExportTemplate.objects.create(
            name=long_name,
            content_type=supplier_ct,
            template_code="long-existing",
        )
        provider_long_template = ExportTemplate.objects.create(
            name=long_name,
            content_type=provider_content_type,
            template_code="long-colliding",
        )
        supplier_saved_filter = SavedFilter.objects.create(
            name=f"Collision filter {suffix}",
            content_type=supplier_ct,
            tenant=cutover_tenant,
            parameters={},
        )
        provider_saved_filter = SavedFilter.objects.create(
            name=f"Collision filter {suffix}",
            content_type=provider_content_type,
            tenant=cutover_tenant,
            parameters={"provider": str(linked_provider.pk)},
        )
        global_filter_one = SavedFilter.objects.create(
            name=f"Global duplicate {suffix}",
            content_type=provider_content_type,
            parameters={},
        )
        global_filter_two = SavedFilter.objects.create(
            name=f"Global duplicate {suffix}",
            content_type=provider_content_type,
            parameters={},
        )
        deleted_provider_filter = SavedFilter.objects.create(
            name=f"Deleted duplicate {suffix}",
            content_type=provider_content_type,
            tenant=cutover_tenant,
            parameters={},
            deleted_at=deleted_at,
        )
        live_provider_filter = SavedFilter.objects.create(
            name=f"Deleted duplicate {suffix}",
            content_type=provider_content_type,
            tenant=cutover_tenant,
            parameters={},
        )
        long_slug = "s" * 255
        long_slug_host = Supplier.objects.create(
            name=f"Slug host {suffix}",
            slug=long_slug,
            tenant=cutover_tenant,
        )
        # Both the preferred slug and slugify(name) collide with the host.
        Provider.objects.create(
            name=long_slug,
            slug=long_slug,
            tenant=cutover_tenant,
        )
        tag_ct = ContentType.objects.get(app_label="extras", model="tag")
        tag_saved_filter = SavedFilter.objects.create(
            name=f"Tag filter {suffix}",
            content_type=tag_ct,
            parameters={"provider": str(linked_provider.pk), "q": "x"},
        )
        UserPreference = old_apps.get_model("users", "UserPreference")
        Notification = old_apps.get_model("core", "Notification")
        user_preference = UserPreference.objects.create(
            user_id=bookmark_user_id,
            data={
                "tables": {
                    "subscriptions": {
                        "SubscriptionTable": {"columns": ["name", "provider", "cost"]},
                        "ProviderTable": {"columns": ["pk", "name", "supplier", "is_active", "unknown_key"]},
                    }
                }
            },
        )
        notification = Notification.objects.create(
            user_id=bookmark_user_id,
            subject=f"Provider changed {suffix}",
            message="body",
            target_url=f"/subscriptions/providers/{linked_provider.pk}/",
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
            "event_rule_id": event_rule.pk,
            "export_template_id": export_template.pk,
            "saved_filter_id": saved_filter.pk,
            "report_template_id": report_template.pk,
            "supplier_export_template_id": supplier_export_template.pk,
            "provider_export_template_id": provider_export_template.pk,
            "supplier_saved_filter_id": supplier_saved_filter.pk,
            "provider_saved_filter_id": provider_saved_filter.pk,
            "tag_saved_filter_id": tag_saved_filter.pk,
            "tag_saved_filter_parameters": {"provider": str(linked_provider.pk), "q": "x"},
            "user_preference_id": user_preference.pk,
            "notification_id": notification.pk,
            "long_name": long_name,
            "supplier_long_template_id": supplier_long_template.pk,
            "provider_long_template_id": provider_long_template.pk,
            "global_filter_one_id": global_filter_one.pk,
            "global_filter_two_id": global_filter_two.pk,
            "deleted_provider_filter_id": deleted_provider_filter.pk,
            "live_provider_filter_id": live_provider_filter.pk,
            "long_slug": long_slug,
            "long_slug_host_id": long_slug_host.pk,
            "cutover_tenant": cutover_tenant.pk,
            "second_tenant": second_tenant.pk,
            "shared_link_subscriptions": shared_link_subscriptions,
            "shared_contact_a_id": shared_contact_a.pk,
            "shared_contact_b_id": shared_contact_b.pk,
            "unrelated_report_template_id": unrelated_report_template.pk,
            "cutover_tenant_id": cutover_tenant.pk,
            "group_journal_entry_id": group_journal_entry.pk,
            "journal_tenant_group_id": journal_tenant_group.pk,
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

        # A shared (global) explicit link must not collapse tenant-specific
        # providers into one cross-tenant supplier: both tenants keep their own
        # scoped incarnation with their own commercial data and contacts, and
        # the shared global supplier carries only the global provider's data.
        shared_link_a = Supplier.objects.get(
            tenant_id=expected["cutover_tenant"], name=f"Shared link vendor {expected['suffix']}"
        )
        shared_link_b = Supplier.objects.get(
            tenant_id=expected["second_tenant"], name=f"Shared link vendor {expected['suffix']}"
        )
        self.assertNotEqual(shared_link_a.pk, shared_link_b.pk)
        self.assertEqual(shared_link_a.account_id, f"tenant-a-{expected['suffix']}")
        self.assertEqual(shared_link_b.account_id, f"tenant-b-{expected['suffix']}")
        self.assertIsNone(shared_link_a.tenant_group_id)
        self.assertIsNone(shared_link_b.tenant_group_id)
        self.assertEqual(
            Subscription.objects.get(pk=expected["shared_link_subscriptions"]["a"]).supplier_id,
            shared_link_a.pk,
        )
        self.assertEqual(
            Subscription.objects.get(pk=expected["shared_link_subscriptions"]["b"]).supplier_id,
            shared_link_b.pk,
        )
        shared_link_assignments = {
            assignment.contact_id: assignment.object_id
            for assignment in ContactAssignment.objects.filter(
                contact_id__in=[expected["shared_contact_a_id"], expected["shared_contact_b_id"]],
                content_type_id=expected["supplier_ct_id"],
            )
        }
        self.assertEqual(shared_link_assignments[expected["shared_contact_a_id"]], shared_link_a.pk)
        self.assertEqual(shared_link_assignments[expected["shared_contact_b_id"]], shared_link_b.pk)
        shared_link_supplier = Supplier.objects.get(pk=expected["linked_supplier"])
        self.assertIsNone(shared_link_supplier.tenant_id)
        self.assertIsNone(shared_link_supplier.tenant_group_id)
        self.assertEqual(shared_link_supplier.account_id, f"linked-{expected['suffix']}")

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

        # Group-scoped history stays bounded: the journal entry inherits the
        # supplier's group instead of degrading into a system-global row.
        group_journaled_supplier = Supplier.objects.get(name=f"Group journaled vendor {expected['suffix']}")
        self.assertIsNone(group_journaled_supplier.tenant_id)
        self.assertEqual(group_journaled_supplier.tenant_group_id, expected["journal_tenant_group_id"])
        group_journal = JournalEntry.objects.get(pk=expected["group_journal_entry_id"])
        self.assertEqual(group_journal.model_id, expected["supplier_ct_id"])
        self.assertEqual(group_journal.object_id, group_journaled_supplier.pk)
        self.assertIsNone(group_journal.tenant_id)
        self.assertEqual(group_journal.tenant_group_id, expected["journal_tenant_group_id"])

        attachment = FileAttachment.objects.get(pk=expected["attachment_id"])
        self.assertEqual(attachment.model_id, expected["supplier_ct_id"])
        self.assertEqual(attachment.object_id, expected["matched_supplier"])

        event = Event.objects.get(pk=expected["event_id"])
        self.assertEqual(event.model_id, expected["supplier_ct_id"])
        self.assertEqual(event.object_id, expected["matched_supplier"])
        self.assertEqual(event.data, {"app_label": "assets", "model_name": "supplier"})

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

        # Durable configuration follows the retired model: event rules and
        # export/saved-filter content-type bindings move to Supplier, report
        # templates rename their columns, and saved filter parameters carry the
        # transplanted supplier id.
        EventRule = self.apps.get_model("extras", "EventRule")
        ExportTemplate = self.apps.get_model("extras", "ExportTemplate")
        SavedFilter = self.apps.get_model("extras", "SavedFilter")
        ReportTemplate = self.apps.get_model("extras", "ReportTemplate")
        event_rule = EventRule.objects.get(pk=expected["event_rule_id"])
        self.assertEqual(event_rule.model_id, expected["supplier_ct_id"])
        export_template = ExportTemplate.objects.get(pk=expected["export_template_id"])
        self.assertEqual(export_template.content_type_id, expected["supplier_ct_id"])
        saved_filter = SavedFilter.objects.get(pk=expected["saved_filter_id"])
        self.assertEqual(saved_filter.content_type_id, expected["supplier_ct_id"])
        self.assertEqual(saved_filter.parameters, {"supplier": str(expected["linked_supplier"])})
        report_template = ReportTemplate.objects.get(pk=expected["report_template_id"])
        self.assertEqual(
            report_template.included_columns,
            ["subscription_name", "supplier", "cost"],
        )
        self.assertEqual(report_template.group_by_field, "supplier")
        self.assertEqual(report_template.template_content, "<p>{{ row['Supplier'] }} / {{ row['Lieferant'] }}</p>")

        # Colliding names move deterministically; unrelated filters keep their
        # parameters; persisted table preferences and notification URLs follow.
        provider_export_template = ExportTemplate.objects.get(pk=expected["provider_export_template_id"])
        self.assertEqual(provider_export_template.name, f"Collision export {expected['suffix']} (Provider 1)")
        self.assertEqual(provider_export_template.content_type_id, expected["supplier_ct_id"])
        self.assertEqual(provider_export_template.template_code, "colliding")
        supplier_export_template = ExportTemplate.objects.get(pk=expected["supplier_export_template_id"])
        self.assertEqual(supplier_export_template.name, f"Collision export {expected['suffix']}")
        provider_saved_filter = SavedFilter.objects.get(pk=expected["provider_saved_filter_id"])
        self.assertEqual(provider_saved_filter.name, f"Collision filter {expected['suffix']} (Provider 1)")
        self.assertEqual(provider_saved_filter.content_type_id, expected["supplier_ct_id"])
        tag_saved_filter = SavedFilter.objects.get(pk=expected["tag_saved_filter_id"])
        self.assertEqual(tag_saved_filter.parameters, expected["tag_saved_filter_parameters"])
        provider_long_template = ExportTemplate.objects.get(pk=expected["provider_long_template_id"])
        self.assertEqual(
            provider_long_template.name,
            f"{expected['long_name'][: 255 - len(' (Provider 1)')]} (Provider 1)",
        )
        self.assertEqual(len(provider_long_template.name), 255)
        supplier_long_template = ExportTemplate.objects.get(pk=expected["supplier_long_template_id"])
        self.assertEqual(supplier_long_template.name, expected["long_name"])
        lower_filter_id, higher_filter_id = sorted([expected["global_filter_one_id"], expected["global_filter_two_id"]])
        # The partial unique constraint treats null tenants as distinct, so the
        # two global filters keep their shared name; only the content type moves.
        for filter_id in (lower_filter_id, higher_filter_id):
            global_filter = SavedFilter.objects.get(pk=filter_id)
            self.assertEqual(global_filter.name, f"Global duplicate {expected['suffix']}")
            self.assertEqual(global_filter.content_type_id, expected["supplier_ct_id"])
        # Soft-deleted rows are excluded from the constraint, so a lower-pk
        # deleted filter no longer forces the live namesake to be renamed.
        deleted_provider_filter = SavedFilter.objects.get(pk=expected["deleted_provider_filter_id"])
        self.assertEqual(deleted_provider_filter.name, f"Deleted duplicate {expected['suffix']}")
        self.assertEqual(deleted_provider_filter.content_type_id, expected["supplier_ct_id"])
        live_provider_filter = SavedFilter.objects.get(pk=expected["live_provider_filter_id"])
        self.assertEqual(live_provider_filter.name, f"Deleted duplicate {expected['suffix']}")
        self.assertEqual(live_provider_filter.content_type_id, expected["supplier_ct_id"])
        # Maximum-length slugs truncate before the collision marker instead of
        # overflowing the 255-character column.
        long_slug_supplier = Supplier.objects.get(slug=f"{expected['long_slug'][:253]}-2")
        self.assertEqual(len(long_slug_supplier.slug), 255)
        self.assertEqual(Supplier.objects.get(pk=expected["long_slug_host_id"]).slug, expected["long_slug"])
        unrelated_report_template = ReportTemplate.objects.get(pk=expected["unrelated_report_template_id"])
        self.assertEqual(unrelated_report_template.template_content, "<p>{{ row['Provider'] }}</p>")
        self.assertEqual(unrelated_report_template.included_columns, ["name"])
        UserPreference = self.apps.get_model("users", "UserPreference")
        Notification = self.apps.get_model("core", "Notification")
        user_preference = UserPreference.objects.get(pk=expected["user_preference_id"])
        self.assertEqual(
            user_preference.data["tables"]["subscriptions"]["SubscriptionTable"]["columns"],
            ["name", "supplier", "cost"],
        )
        self.assertEqual(
            user_preference.data["tables"]["assets"]["SupplierTable"]["columns"],
            ["pk", "name", "is_active"],
        )
        self.assertNotIn("ProviderTable", user_preference.data["tables"]["subscriptions"])
        notification = Notification.objects.get(pk=expected["notification_id"])
        self.assertEqual(notification.target_url, f"/assets/suppliers/{expected['linked_supplier']}/")
        self.assertEqual(
            export_template.template_code,
            "{{ queryset.first().notes }}|{{ queryset.first().name }}|{{ queryset.first()['notes'] }}"
            "|{{ queryset.first().subscriptions.first().supplier.name }}",
        )

        with self.assertRaises(LookupError):
            self.apps.get_model("subscriptions", "Provider")



@pytest.mark.serial_only
class UnifiedVendorCutoverDanglingReferenceTests(TransactionTestCase):
    """Dangling provider references (deleted providers, historical rows) must
    not abort the cutover: the affected rows are kept as-is."""

    def setUp(self):
        super().setUp()
        self.addCleanup(self._restore_leaf)
        self.executor = self._scoped_executor()
        self.executor.migrate([MIGRATE_FROM, ASSET_STATE])
        old_apps = self.executor.loader.project_state([MIGRATE_FROM, ASSET_STATE]).apps

        Provider = old_apps.get_model("subscriptions", "Provider")
        JournalEntry = old_apps.get_model("extras", "JournalEntry")
        Event = old_apps.get_model("extras", "Event")
        Contact = old_apps.get_model("organization", "Contact")
        ContactRole = old_apps.get_model("organization", "ContactRole")
        ContactAssignment = old_apps.get_model("organization", "ContactAssignment")
        ContentType = old_apps.get_model("contenttypes", "ContentType")

        suffix = uuid.uuid4().hex[:8]
        provider_ct, _ = ContentType.objects.get_or_create(app_label="subscriptions", model="provider")
        live_provider = Provider.objects.create(
            name=f"Dangling neighbour {suffix}", slug=f"dangling-neighbour-{suffix}"
        )
        missing_id = 999999

        dangling_journal = JournalEntry.objects.create(
            model=provider_ct,
            object_id=missing_id,
            comment="Dangling provider journal comment",
        )
        dangling_event = Event.objects.create(
            model=provider_ct,
            object_id=missing_id,
            action="create",
            data={"app_label": "subscriptions", "model_name": "provider"},
        )
        role = ContactRole.objects.create(name=f"Dangling role {suffix}", slug=f"dangling-role-{suffix}")
        contact = Contact.objects.create(name=f"Dangling contact {suffix}")
        dangling_assignment = ContactAssignment.objects.create(
            contact=contact,
            role=role,
            content_type=provider_ct,
            object_id=missing_id,
        )

        self.expected = {
            "provider_ct_id": provider_ct.pk,
            "live_provider_id": live_provider.pk,
            "dangling_journal_id": dangling_journal.pk,
            "dangling_event_id": dangling_event.pk,
            "dangling_assignment_id": dangling_assignment.pk,
            "missing_id": missing_id,
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

    def test_cutover_tolerates_dangling_provider_references(self):
        JournalEntry = self.apps.get_model("extras", "JournalEntry")
        Event = self.apps.get_model("extras", "Event")
        ContactAssignment = self.apps.get_model("organization", "ContactAssignment")

        expected = self.expected
        journal = JournalEntry.objects.get(pk=expected["dangling_journal_id"])
        self.assertEqual(journal.model_id, expected["provider_ct_id"])
        self.assertEqual(journal.object_id, expected["missing_id"])
        event = Event.objects.get(pk=expected["dangling_event_id"])
        self.assertEqual(event.model_id, expected["provider_ct_id"])
        self.assertEqual(event.object_id, expected["missing_id"])
        assignment = ContactAssignment.objects.get(pk=expected["dangling_assignment_id"])
        self.assertEqual(assignment.content_type_id, expected["provider_ct_id"])
        self.assertEqual(assignment.object_id, expected["missing_id"])
