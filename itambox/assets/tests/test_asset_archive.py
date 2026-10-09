"""Per-aggregate archive service for Asset (#619, step 4b).

Pins the approved behaviour table: REFUSE while the asset is assigned, in open
maintenance or live reservation; ARCHIVE of finished history with the operation
marker (restore brings back exactly those rows); DETACH of open requests; KEEP of
disposals; fail-closed outside the active tenant; the delete view and the
recycle-bin restore reach the service.
"""

from contextlib import contextmanager
from datetime import date, timedelta
from uuid import uuid4

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse
from model_bakery import baker

from assets.archive_services import archive_asset, restore_asset
from assets.models import (
    Asset,
    AssetAssignment,
    AssetMaintenance,
    AssetReservation,
    StatusLabel,
    Warranty,
)
from assets.models.choices import MaintenanceStatusChoices, ReservationStatusChoices
from core.archive_handlers import ArchiveBlocked
from core.models import ObjectChange
from core.tests.mixins import TenantTestMixin
from itambox.middleware import _current_user, _request_id
from organization.models import AssetHolder, Tenant


@contextmanager
def acting_as(user=None):
    request_token = _request_id.set(uuid4())
    user_token = _current_user.set(user)
    try:
        yield
    finally:
        _request_id.reset(request_token)
        _current_user.reset(user_token)


def object_changes(instance, action=None):
    content_type = ContentType.objects.get_for_model(instance.__class__)
    rows = ObjectChange._base_manager.filter(changed_object_type=content_type, changed_object_id=instance.pk)
    return rows.filter(action=action) if action else rows


class AssetArchiveFixtureMixin:
    def make_asset(self, **extra):
        values = {"name": f"P619 Asset {uuid4().hex[:6]}", "status": self.status, "tenant": self.tenant}
        values.update(extra)
        return baker.make(Asset, **values)

    def make_holder(self):
        return AssetHolder.objects.create(
            first_name="Pia", last_name="Archiviert", tenant=self.tenant, upn=f"p619a.{uuid4().hex[:8]}@example.test"
        )

    def deploy(self, asset):
        deployed = baker.make(StatusLabel, type="deployed")
        Asset.objects.filter(pk=asset.pk).update(status=deployed)
        asset.refresh_from_db()

    def make_assignment(self, asset, active=True):
        if active:
            self.deploy(asset)
        return AssetAssignment.objects.create(asset=asset, assigned_user=self.make_holder(), is_active=active)

    def make_maintenance(self, asset, status):
        return AssetMaintenance.objects.create(asset=asset, status=status, start_date=date.today())

    def make_warranty(self, asset):
        return Warranty.objects.create(
            asset=asset, start_date=date.today(), end_date=date.today() + timedelta(days=365)
        )

    def make_reservation(self, asset, status):
        return AssetReservation.objects.create(
            asset=asset,
            status=status,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=1),
        )


class AssetArchiveServiceTests(AssetArchiveFixtureMixin, TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="P619a Tenant", slug="p619a-tenant")
        self.status = baker.make(StatusLabel, type="deployable")
        self.asset = self.make_asset()

    def archive(self, asset=None):
        with self.tenant_context(self.tenant), acting_as(self.tenant_admin):
            return archive_asset(asset or self.asset)

    def restore(self, asset=None):
        with self.tenant_context(self.tenant), acting_as(self.tenant_admin):
            return restore_asset(asset or self.asset)

    def test_clean_asset_is_archived_and_audited(self):
        result = self.archive()

        self.assertEqual((result.archived, result.detached), (1, 0))
        self.assertIsNotNone(result.operation_id)
        self.asset.refresh_from_db()
        self.assertIsNotNone(self.asset.deleted_at)
        self.assertFalse(Asset.objects.filter(pk=self.asset.pk).exists())
        self.assertEqual(object_changes(self.asset, action="delete").count(), 1)

    def test_archive_is_idempotent(self):
        self.archive()
        second = self.archive()

        self.assertEqual(second.archived, 0)
        self.assertEqual(object_changes(self.asset, action="delete").count(), 1)

    def test_history_is_archived_with_marker_and_restored(self):
        closed = self.make_assignment(self.asset, active=False)
        done = self.make_maintenance(self.asset, MaintenanceStatusChoices.COMPLETED)
        warranty = self.make_warranty(self.asset)
        finished = self.make_reservation(self.asset, ReservationStatusChoices.FULFILLED)

        result = self.archive()

        self.assertEqual(result.archived, 5)
        for row in (closed, done, warranty, finished):
            archived = type(row).all_objects.get(pk=row.pk)
            self.assertIsNotNone(archived.deleted_at)
            self.assertEqual(archived.archive_operation_id, result.operation_id)

        self.restore()

        self.asset.refresh_from_db()
        self.assertIsNone(self.asset.deleted_at)
        for row in (closed, done, warranty, finished):
            restored = type(row).all_objects.get(pk=row.pk)
            self.assertIsNone(restored.deleted_at)
            self.assertIsNone(restored.archive_operation_id)

    def test_restore_leaves_independently_deleted_rows_deleted(self):
        warranty = self.make_warranty(self.asset)
        warranty.delete()
        self.archive()

        self.restore()

        self.assertIsNotNone(Warranty.all_objects.get(pk=warranty.pk).deleted_at)

    def test_active_assignment_refuses_without_writing(self):
        assignment = self.make_assignment(self.asset)

        with self.assertRaises(ArchiveBlocked) as caught:
            self.archive()

        self.assertIn("active assignments", repr(caught.exception.blockers))
        self.asset.refresh_from_db()
        self.assertIsNone(self.asset.deleted_at)
        self.assertIsNone(AssetAssignment.all_objects.get(pk=assignment.pk).deleted_at)
        self.assertEqual(object_changes(self.asset).count(), 0)

    def test_assignment_as_target_refuses(self):
        host = self.make_asset()
        self.deploy(host)
        AssetAssignment.objects.create(asset=host, assigned_asset=self.asset, is_active=True)

        with self.assertRaises(ArchiveBlocked):
            self.archive()

    def test_open_maintenance_refuses(self):
        self.make_maintenance(self.asset, MaintenanceStatusChoices.IN_PROGRESS)

        with self.assertRaises(ArchiveBlocked) as caught:
            self.archive()

        self.assertIn("open maintenance", repr(caught.exception.blockers))

    def test_live_reservation_refuses(self):
        self.make_reservation(self.asset, ReservationStatusChoices.ACTIVE)

        with self.assertRaises(ArchiveBlocked) as caught:
            self.archive()

        self.assertIn("live reservations", repr(caught.exception.blockers))

    def test_archive_fails_closed_outside_the_active_tenant(self):
        other = Tenant.objects.create(name="P619a Other", slug="p619a-other")
        foreign = self.make_asset(tenant=other)

        with self.tenant_context(self.tenant), acting_as(self.tenant_admin):
            with self.assertRaises(PermissionDenied):
                archive_asset(foreign)

        foreign.refresh_from_db()
        self.assertIsNone(foreign.deleted_at)


class AssetArchiveSurfaceTests(AssetArchiveFixtureMixin, TenantTestMixin, TestCase):
    PERMISSIONS = ["assets.view_asset", "assets.delete_asset", "core.view_recyclebin", "core.change_recyclebin"]

    def setUp(self):
        self.setup_tenant_context(name="P619a Surface", slug="p619a-surface", permissions=list(self.PERMISSIONS))
        self.status = baker.make(StatusLabel, type="deployable")
        self.asset = self.make_asset()
        self.client.force_login(self.tenant_admin)

    def test_ui_delete_archives_through_the_service(self):
        history = self.make_warranty(self.asset)

        response = self.client.post(reverse("assets:asset_delete", kwargs={"pk": self.asset.pk}))

        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(Asset.all_objects.get(pk=self.asset.pk).deleted_at)
        self.assertIsNotNone(Warranty.all_objects.get(pk=history.pk).archive_operation_id)

    def test_ui_delete_refusal_keeps_the_asset(self):
        self.make_assignment(self.asset)

        response = self.client.post(reverse("assets:asset_delete", kwargs={"pk": self.asset.pk}))

        self.assertEqual(response.status_code, 302)
        self.assertIsNone(Asset.all_objects.get(pk=self.asset.pk).deleted_at)

    def test_recycle_bin_restore_uses_the_service(self):
        with self.tenant_context(self.tenant), acting_as(self.tenant_admin):
            archive_asset(self.asset)
        content_type = ContentType.objects.get_for_model(Asset)

        response = self.client.post(
            reverse("object_restore", kwargs={"content_type_id": content_type.pk, "object_id": self.asset.pk})
        )

        self.assertEqual(response.status_code, 302)
        self.assertIsNone(Asset.all_objects.get(pk=self.asset.pk).deleted_at)
