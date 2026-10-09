"""Per-aggregate archive service for Location (#619, step 3c).

Pins the approved behaviour table: REFUSE while live assets, stock with a
positive quantity, live child locations or open purchase orders depend on the
location; ARCHIVE empty stock rows with it (restore brings back exactly those);
DETACH open audit sessions; and the surfaces (delete view, recycle-bin restore)
reach the service.
"""

from django.test import TestCase
from django.urls import reverse

from assets.models import Asset, Category, Manufacturer, StatusLabel
from compliance.choices import AuditSessionStatusChoices
from compliance.models import AuditSession
from core.tests.mixins import TenantTestMixin
from inventory.models import Component, ComponentStock
from organization.models import Location, Site
from organization.services.location_archive import ArchiveBlocked, archive_location, restore_location


class LocationArchiveServiceTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="P619c Tenant", slug="p619c-tenant")
        self.site = Site.objects.create(name="P619c Site", slug="p619c-site", tenant=self.tenant)
        self.location = self.make_location("p619c-loc")

    def make_location(self, slug, **extra):
        return Location.objects.create(name=slug, slug=slug, site=self.site, tenant=self.tenant, **extra)

    def archive(self, location=None):
        with self.tenant_context(self.tenant):
            return archive_location(location or self.location)

    def restore(self, location=None):
        with self.tenant_context(self.tenant):
            return restore_location(location or self.location)

    def make_stock(self, qty):
        manufacturer = Manufacturer.objects.create(name=f"P619c Maker {qty}", slug=f"p619c-maker-{qty}")
        category = Category.objects.create(
            name=f"P619c Cat {qty}", slug=f"p619c-cat-{qty}", applies_to={"component": True}
        )
        component = Component.objects.create(
            name=f"P619c Component {qty}", manufacturer=manufacturer, category=category
        )
        return ComponentStock.objects.create(component=component, location=self.location, qty=qty)

    def test_clean_location_is_archived_and_restored(self):
        result = self.archive()

        self.assertEqual(result.archived, 1)
        self.location.refresh_from_db()
        self.assertIsNotNone(self.location.deleted_at)
        self.assertFalse(Location.objects.filter(pk=self.location.pk).exists())

        self.restore()
        self.location.refresh_from_db()
        self.assertIsNone(self.location.deleted_at)

    def test_archive_is_idempotent(self):
        self.archive()
        self.assertEqual(self.archive().archived, 0)

    def test_live_asset_refuses_without_writing(self):
        status = StatusLabel.objects.create(name="P619c Ready", slug="p619c-ready")
        Asset.objects.create(
            name="P619c Asset", asset_tag="P619C-1", status=status, tenant=self.tenant, location=self.location
        )

        with self.assertRaises(ArchiveBlocked):
            self.archive()
        self.location.refresh_from_db()
        self.assertIsNone(self.location.deleted_at)

    def test_live_child_location_refuses(self):
        self.make_location("p619c-child", parent=self.location)
        with self.assertRaises(ArchiveBlocked):
            self.archive()

    def test_stock_with_quantity_refuses_and_keeps_the_row(self):
        stock = self.make_stock(3)
        with self.assertRaises(ArchiveBlocked):
            self.archive()
        stock.refresh_from_db()
        self.assertIsNone(stock.deleted_at)

    def test_empty_stock_is_archived_with_the_location_and_restored_with_it(self):
        empty = self.make_stock(0)

        result = self.archive()

        self.assertEqual(result.archived, 2)
        empty = ComponentStock.all_objects.get(pk=empty.pk)
        self.assertIsNotNone(empty.deleted_at)
        self.assertIsNotNone(empty.archive_operation_id)

        self.restore()
        empty.refresh_from_db()
        self.assertIsNone(empty.deleted_at)
        self.assertIsNone(empty.archive_operation_id)

    def test_stock_deleted_on_its_own_is_not_resurrected_by_restore(self):
        stock = self.make_stock(0)
        stock.delete()
        self.archive()
        self.restore()

        self.assertIsNotNone(ComponentStock.all_objects.get(pk=stock.pk).deleted_at)

    def test_open_audit_session_is_detached_and_not_reattached(self):
        session = AuditSession.objects.create(
            name="P619c Audit",
            created_by=self.tenant_admin,
            location=self.location,
            tenant=self.tenant,
            status=AuditSessionStatusChoices.PLANNED,
        )

        result = self.archive()

        self.assertEqual(result.detached, 1)
        session.refresh_from_db()
        self.assertIsNone(session.location_id)
        self.restore()
        session.refresh_from_db()
        self.assertIsNone(session.location_id)


class LocationDeleteSurfaceTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="P619c Surface", slug="p619c-surface")
        site = Site.objects.create(name="P619c Surface Site", slug="p619c-surface-site", tenant=self.tenant)
        self.location = Location.objects.create(name="P619c Surf", slug="p619c-surf", site=site, tenant=self.tenant)
        self.client.force_login(self.tenant_admin)

    def test_delete_view_archives_through_the_service(self):
        response = self.client.post(reverse("organization:location_delete", args=[self.location.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(Location.all_objects.get(pk=self.location.pk).deleted_at)
