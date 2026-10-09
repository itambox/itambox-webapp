"""Per-aggregate archive service for License (#619, step 4).

Pins the approved behaviour table: REFUSE while a seat is still held (no
auto-release) or a live kit item lists the license; the released seats stay
archived; the purchase order lines, journal entries and attachments stay as
evidence; restore brings back the license only. Also pins that the UI delete
view and the recycle-bin restore reach the service and that the service fails
closed outside the active tenant.
"""

from contextlib import contextmanager
from uuid import uuid4

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from assets.models import Manufacturer
from core.archive_handlers import ArchiveBlocked
from core.models import ObjectChange
from core.tests.mixins import TenantTestMixin
from inventory.models import Kit, KitItem
from itambox.middleware import _current_user, _request_id
from licenses.archive_services import archive_license, restore_license
from licenses.models import License, LicenseSeatAssignment
from organization.models import AssetHolder, Tenant
from software.models import Software


@contextmanager
def acting_as(user=None):
    """Enter the request audit context the archive service writes into."""
    request_token = _request_id.set(uuid4())
    user_token = _current_user.set(user)
    try:
        yield
    finally:
        _request_id.reset(request_token)
        _current_user.reset(user_token)


def object_changes(instance, action=None):
    """Audit rows written for one instance."""
    content_type = ContentType.objects.get_for_model(instance.__class__)
    rows = ObjectChange._base_manager.filter(
        changed_object_type=content_type,
        changed_object_id=instance.pk,
    )
    return rows.filter(action=action) if action else rows


class LicenseArchiveFixtureMixin:
    """Fixtures shared by the service-level and surface-level suites."""

    def make_license(self, name="P619 License", **extra):
        owner = extra.get("tenant", self.tenant)
        software = Software.objects.create(
            name=f"P619 Software {uuid4().hex[:8]}",
            manufacturer=Manufacturer.objects.create(name=f"Mfg {uuid4().hex[:8]}", slug=f"mfg-{uuid4().hex[:8]}"),
            tenant=owner,
        )
        values = {"name": name, "software": software, "seats": 10, "tenant": self.tenant}
        values.update(extra)
        return License.objects.create(**values)

    def make_holder(self):
        return AssetHolder.objects.create(
            first_name="Pia",
            last_name="Archiviert",
            tenant=self.tenant,
            upn=f"p619e.{uuid4().hex[:8]}@example.test",
        )

    def make_held_seat(self, license_row):
        return LicenseSeatAssignment.objects.create(license=license_row, assigned_holder=self.make_holder())

    def make_released_seat(self, license_row):
        seat = self.make_held_seat(license_row)
        seat.delete()
        return seat

    def make_kit_item(self, license_row):
        kit = Kit.objects.create(name=f"Kit {uuid4().hex[:8]}", tenant=self.tenant)
        return KitItem.objects.create(kit=kit, qty=1, license=license_row)


class LicenseArchiveServiceTests(LicenseArchiveFixtureMixin, TenantTestMixin, TestCase):
    """``archive_license`` / ``restore_license`` on their own."""

    def setUp(self):
        self.setup_tenant_context(name="P619e Tenant", slug="p619e-tenant")
        self.license = self.make_license()

    def archive(self, license_row=None, actor=None):
        with self.tenant_context(self.tenant), acting_as(actor or self.tenant_admin):
            return archive_license(license_row or self.license)

    def restore(self, license_row=None, actor=None):
        with self.tenant_context(self.tenant), acting_as(actor or self.tenant_admin):
            return restore_license(license_row or self.license)

    # ------------------------------------------------------------- happy path
    def test_clean_license_is_archived_and_audited(self):
        result = self.archive()

        self.assertEqual((result.archived, result.detached, result.kept), (1, 0, 0))
        self.assertIsNotNone(result.operation_id)
        self.license.refresh_from_db()
        self.assertIsNotNone(self.license.deleted_at)
        self.assertFalse(License.objects.filter(pk=self.license.pk).exists())
        self.assertTrue(License.all_objects.filter(pk=self.license.pk).exists())
        self.assertEqual(object_changes(self.license, action="delete").count(), 1)

    def test_archive_keeps_a_released_seat_archived(self):
        seat = self.make_released_seat(self.license)

        result = self.archive()

        self.assertEqual(result.kept, 1)
        self.assertIsNotNone(LicenseSeatAssignment.all_objects.get(pk=seat.pk).deleted_at)

    def test_archive_is_idempotent_and_audits_once(self):
        self.archive()
        second = self.archive()

        self.assertEqual(second.archived, 0)
        self.assertEqual(object_changes(self.license, action="delete").count(), 1)

    def test_restore_brings_the_license_back_and_leaves_released_seats_released(self):
        seat = self.make_released_seat(self.license)
        self.archive()

        self.restore()

        self.license.refresh_from_db()
        self.assertIsNone(self.license.deleted_at)
        self.assertTrue(License.objects.filter(pk=self.license.pk).exists())
        self.assertEqual(object_changes(self.license, action="update").count(), 1)
        # A released seat is never resurrected as a held seat behind the caller's back.
        self.assertIsNotNone(LicenseSeatAssignment.all_objects.get(pk=seat.pk).deleted_at)
        self.assertEqual(self.license.assignments.count(), 0)

    # ------------------------------------------------------------- refusal
    def test_held_seat_refuses_without_writing(self):
        seat = self.make_held_seat(self.license)

        with self.assertRaises(ArchiveBlocked) as caught:
            self.archive()

        self.assertIn("held seats", repr(caught.exception.blockers))
        self.license.refresh_from_db()
        self.assertIsNone(self.license.deleted_at)
        self.assertIsNone(LicenseSeatAssignment.all_objects.get(pk=seat.pk).deleted_at)
        self.assertEqual(object_changes(self.license).count(), 0)

    def test_live_kit_item_refuses_without_writing(self):
        kit_item = self.make_kit_item(self.license)

        with self.assertRaises(ArchiveBlocked) as caught:
            self.archive()

        self.assertIn("live kit items", repr(caught.exception.blockers))
        self.license.refresh_from_db()
        self.assertIsNone(self.license.deleted_at)
        kit_item.refresh_from_db()
        self.assertIsNone(kit_item.deleted_at)

    # ------------------------------------------------------------- tenant boundary
    def test_archive_fails_closed_for_a_license_outside_the_active_tenant(self):
        other = Tenant.objects.create(name="P619e Other", slug="p619e-other")
        foreign = self.make_license(name="P619e Foreign", tenant=other)

        with self.tenant_context(self.tenant), acting_as(self.tenant_admin):
            with self.assertRaises(PermissionDenied):
                archive_license(foreign)

        foreign.refresh_from_db()
        self.assertIsNone(foreign.deleted_at)


class LicenseArchiveSurfaceTests(LicenseArchiveFixtureMixin, TenantTestMixin, TestCase):
    """The delete view and the recycle-bin restore reach the aggregate service."""

    LICENSE_PERMISSIONS = [
        "licenses.view_license",
        "licenses.delete_license",
        "licenses.view_licenseseatassignment",
        "core.view_recyclebin",
        "core.change_recyclebin",
    ]

    def setUp(self):
        self.setup_tenant_context(
            name="P619e Surface",
            slug="p619e-surface",
            permissions=list(self.LICENSE_PERMISSIONS),
        )
        self.license = self.make_license()
        self.client.force_login(self.tenant_admin)

    def test_ui_delete_archives_through_the_service(self):
        response = self.client.post(reverse("licenses:license_delete", kwargs={"pk": self.license.pk}))

        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(License.all_objects.get(pk=self.license.pk).deleted_at)

    def test_ui_delete_refusal_keeps_the_license(self):
        self.make_held_seat(self.license)

        response = self.client.post(reverse("licenses:license_delete", kwargs={"pk": self.license.pk}), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(License.all_objects.get(pk=self.license.pk).deleted_at)

    def test_recycle_bin_restore_uses_the_service(self):
        with self.tenant_context(self.tenant), acting_as(self.tenant_admin):
            archive_license(self.license)
        content_type = ContentType.objects.get_for_model(License)

        response = self.client.post(
            reverse("object_restore", kwargs={"content_type_id": content_type.pk, "object_id": self.license.pk})
        )

        self.assertEqual(response.status_code, 302)
        self.assertIsNone(License.all_objects.get(pk=self.license.pk).deleted_at)
