"""Per-aggregate archive service for Tenant (#619, step 5).

Pins the approved behaviour table: REFUSE while live business records or live
child tenants exist; ARCHIVE the soft configuration rows (restore brings back
exactly those); DETACH resource grants; KEEP memberships and tokens; and the
delete view reaches the service.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from extras.models import SavedFilter
from organization.models import Membership, Role, Site, Tenant
from organization.services.tenant_archive import ArchiveBlocked, archive_tenant, restore_tenant

User = get_user_model()


class TenantArchiveServiceTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="p619t-admin", password="x", is_superuser=True, is_staff=True)
        self.tenant = Tenant.objects.create(name="P619t Tenant", slug="p619t-tenant", is_provider=True)

    def archive(self, tenant=None):
        return archive_tenant(tenant or self.tenant, actor=self.admin)

    def test_empty_tenant_is_archived_and_restored(self):
        result = self.archive()

        self.assertEqual(result.archived, 1)
        self.tenant.refresh_from_db()
        self.assertIsNotNone(self.tenant.deleted_at)
        self.assertFalse(Tenant.objects.filter(pk=self.tenant.pk).exists())

        restore_tenant(self.tenant, actor=self.admin)
        self.tenant.refresh_from_db()
        self.assertIsNone(self.tenant.deleted_at)

    def test_archive_is_idempotent(self):
        self.archive()
        self.assertEqual(self.archive().archived, 0)

    def test_live_site_refuses_without_writing(self):
        Site.objects.create(name="P619t Site", slug="p619t-site", tenant=self.tenant)

        with self.assertRaises(ArchiveBlocked):
            self.archive()
        self.tenant.refresh_from_db()
        self.assertIsNone(self.tenant.deleted_at)

    def test_live_managed_child_tenant_refuses(self):
        Tenant.objects.create(name="P619t Child", slug="p619t-child", managed_by=self.tenant)
        with self.assertRaises(ArchiveBlocked):
            self.archive()

    def test_membership_is_kept_through_archive_and_restore(self):
        member = User.objects.create_user(username="p619t-member", password="x")
        membership = Membership.objects.create(user=member, tenant=self.tenant)

        self.archive()
        self.assertTrue(Membership.objects.filter(pk=membership.pk).exists())

        restore_tenant(self.tenant, actor=self.admin)
        self.assertTrue(Membership.objects.filter(pk=membership.pk, is_active=True).exists())

    def test_configuration_rows_are_archived_and_restored_with_the_tenant(self):
        role = Role.all_objects.create(name="P619t Role", tenant=self.tenant)
        archived_before = Role.all_objects.create(name="P619t Old Role", tenant=self.tenant)
        archived_before.delete()
        before_deleted_at = Role.all_objects.get(pk=archived_before.pk).deleted_at

        result = self.archive()

        self.assertGreaterEqual(result.archived, 2)
        role.refresh_from_db()
        self.assertIsNotNone(role.deleted_at)
        self.assertIsNotNone(role.archive_operation_id)

        restore_tenant(self.tenant, actor=self.admin)
        role.refresh_from_db()
        self.assertIsNone(role.deleted_at)
        self.assertIsNone(role.archive_operation_id)
        # a row archived before the tenant stays archived
        old = Role.all_objects.get(pk=archived_before.pk)
        self.assertEqual(old.deleted_at, before_deleted_at)

    def test_saved_filter_is_archived_with_the_tenant(self):
        from django.contrib.contenttypes.models import ContentType

        saved = SavedFilter.objects.create(
            name="P619t Filter",
            content_type=ContentType.objects.get_for_model(Site),
            parameters={},
            tenant=self.tenant,
        )
        self.archive()
        saved.refresh_from_db()
        self.assertIsNotNone(saved.deleted_at)

    def test_restore_refuses_when_name_was_reused(self):
        self.archive()
        Tenant.objects.create(name="P619t Tenant", slug="p619t-other")
        with self.assertRaises(ArchiveBlocked):
            restore_tenant(self.tenant, actor=self.admin)
        self.tenant.refresh_from_db()
        self.assertIsNotNone(self.tenant.deleted_at)


class TenantArchiveViewTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="p619t-view", password="x", is_superuser=True, is_staff=True)
        self.client.force_login(self.admin)
        self.tenant = Tenant.objects.create(name="P619t View", slug="p619t-view")

    def test_delete_view_archives_empty_tenant(self):
        response = self.client.post(reverse("organization:tenant_delete", kwargs={"pk": self.tenant.pk}))

        self.assertEqual(response.status_code, 302)
        self.tenant.refresh_from_db()
        self.assertIsNotNone(self.tenant.deleted_at)

    def test_delete_view_reports_the_refusal_and_keeps_the_tenant(self):
        Site.objects.create(name="P619t View Site", slug="p619t-view-site", tenant=self.tenant)

        response = self.client.post(reverse("organization:tenant_delete", kwargs={"pk": self.tenant.pk}))

        self.assertEqual(response.status_code, 302)
        self.tenant.refresh_from_db()
        self.assertIsNone(self.tenant.deleted_at)
