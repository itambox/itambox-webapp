"""Per-aggregate archive for PurchaseOrder (#619 step 3b).

Deleting a purchase order is an archive of an aggregate root. These tests pin:

- the archive moves the lines with the order (one audited delete per row) and
  detaches the contracts that originated from it (one audited update each);
- idempotent re-archive;
- an order whose line still has a live fulfillment link is refused with a typed
  error listing the blockers, and nothing is written;
- restore brings back the order and the lines the operation archived, never a
  line removed on its own, and never re-attaches a detached contract;
- a restore refused by the active order-number slot leaves everything archived;
- atomicity: a failure after the child writes rolls the whole archive back;
- tenant isolation of the service;
- that the UI delete, the REST API delete and the recycle-bin
  restore all reach the service.
"""

import datetime
from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

from django.contrib.contenttypes.models import ContentType
from django.contrib.messages import get_messages
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from assets.choices import RequestStatusChoices
from assets.models import AssetRequest, AssetType, Manufacturer, Supplier
from core.archive_handlers import ArchiveBlocked
from core.models import ObjectChange
from core.tests.mixins import TenantTestMixin
from itambox.middleware import _current_user, _request_id
from organization.models import Location, Site, Tenant
from procurement.archive_services import archive_purchase_order, restore_purchase_order
from procurement.models import Contract, FulfillmentLink, PurchaseOrder, PurchaseOrderLine


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


class PurchaseOrderArchiveFixtureMixin:
    """Fixtures shared by the service-level and surface-level suites."""

    def make_reference_data(self):
        self.site = Site.objects.create(name="P619 PO Site", slug="p619-po-site")
        self.location = Location.objects.create(
            name="P619 PO Location", slug="p619-po-location", site=self.site, tenant=self.tenant
        )
        self.supplier = Supplier.objects.create(name="P619 PO Supplier", slug="p619-po-supplier")
        self.asset_type = AssetType.objects.create(
            manufacturer=Manufacturer.objects.create(name="P619 PO Mfg", slug="p619-po-mfg"),
            model="P619 PO Laptop",
            slug="p619-po-laptop",
            requestable=True,
        )

    def make_order(self, order_number="PO-619-1", tenant=None, location=None, **extra):
        values = {
            "tenant": tenant or self.tenant,
            "order_number": order_number,
            "supplier": self.supplier,
            "destination_location": location or self.location,
        }
        values.update(extra)
        return PurchaseOrder.objects.create(**values)

    def make_line(self, order, **extra):
        values = {"tenant": order.tenant, "purchase_order": order, "asset_type": self.asset_type, "qty_ordered": 1}
        values.update(extra)
        return PurchaseOrderLine.objects.create(**values)

    def make_contract(self, order, number="CTR-619-1"):
        return Contract.objects.create(
            name=f"P619 Contract {number}",
            contract_number=number,
            contract_type="support",
            status="active",
            supplier=self.supplier,
            tenant=order.tenant,
            purchase_order=order,
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2027, 1, 1),
        )

    def make_link(self, line):
        request = AssetRequest.objects.create(
            tenant=line.tenant,
            requester=self.tenant_admin,
            asset_type=self.asset_type,
            status=RequestStatusChoices.PROCUREMENT,
        )
        return FulfillmentLink.objects.create(
            tenant=line.tenant, asset_request=request, purchase_order_line=line, qty_allocated=1
        )


class PurchaseOrderArchiveServiceTests(PurchaseOrderArchiveFixtureMixin, TenantTestMixin, TestCase):
    """`archive_purchase_order` / `restore_purchase_order` on their own."""

    def setUp(self):
        self.setup_tenant_context(name="P619 PO Tenant", slug="p619-po-tenant")
        self.make_reference_data()
        self.order = self.make_order()
        self.first_line = self.make_line(self.order)
        self.second_line = self.make_line(self.order)
        self.contract = self.make_contract(self.order)

    def archive(self, order=None, actor=None):
        with self.tenant_context(self.tenant), acting_as(actor or self.tenant_admin):
            return archive_purchase_order(order or self.order)

    def restore(self, order=None, actor=None):
        with self.tenant_context(self.tenant), acting_as(actor or self.tenant_admin):
            return restore_purchase_order(order or self.order)

    # ------------------------------------------------------------- happy path
    def test_archive_moves_the_lines_and_detaches_the_contracts(self):
        result = self.archive()

        self.assertEqual(result.archived, 3)
        self.assertEqual(result.detached, 1)
        self.assertIsNotNone(result.operation_id)

        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.deleted_at)
        self.assertFalse(PurchaseOrder.objects.filter(pk=self.order.pk).exists())
        self.assertEqual(object_changes(self.order, action="delete").count(), 1)

        for line in (self.first_line, self.second_line):
            line.refresh_from_db()
            self.assertEqual(line.deleted_at, self.order.deleted_at)
            self.assertEqual(object_changes(line, action="delete").count(), 1)
        self.assertEqual(PurchaseOrderLine.all_objects.filter(purchase_order=self.order).count(), 2)

        self.contract.refresh_from_db()
        self.assertIsNone(self.contract.purchase_order)
        self.assertIsNone(self.contract.deleted_at)
        self.assertEqual(object_changes(self.contract, action="update").count(), 1)

    def test_archive_is_idempotent_and_audits_once(self):
        self.archive()
        second = self.archive()

        self.assertEqual(second.archived, 0)
        self.assertEqual(object_changes(self.order, action="delete").count(), 1)
        self.assertEqual(object_changes(self.first_line, action="delete").count(), 1)

    # ------------------------------------------------------------- refusal
    def test_archive_is_refused_while_a_line_has_a_live_fulfillment_link(self):
        link = self.make_link(self.first_line)

        with self.assertRaises(ArchiveBlocked) as caught:
            self.archive()

        self.assertEqual(list(caught.exception.blockers), [link])
        self.assertIn(self.order.order_number, caught.exception.headline)
        # No partial write: the order, both lines and the contract are untouched.
        self.order.refresh_from_db()
        self.assertIsNone(self.order.deleted_at)
        for line in (self.first_line, self.second_line):
            line.refresh_from_db()
            self.assertIsNone(line.deleted_at)
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.purchase_order_id, self.order.pk)
        self.assertEqual(object_changes(self.order).count(), 0)
        self.assertEqual(object_changes(self.first_line).count(), 0)
        self.assertEqual(object_changes(self.contract).count(), 0)

    def test_archive_proceeds_once_the_link_is_released(self):
        link = self.make_link(self.first_line)
        with self.tenant_context(self.tenant), acting_as(self.tenant_admin):
            link.delete()

        result = self.archive()

        self.assertEqual(result.archived, 3)

    # ------------------------------------------------------------- restore
    def test_restore_brings_back_the_order_and_the_archived_lines(self):
        self.archive()
        self.restore()

        self.order.refresh_from_db()
        self.assertIsNone(self.order.deleted_at)
        self.assertEqual(object_changes(self.order, action="update").count(), 1)
        for line in (self.first_line, self.second_line):
            line.refresh_from_db()
            self.assertIsNone(line.deleted_at)
            self.assertEqual(object_changes(line, action="update").count(), 1)
        self.assertEqual(self.order.lines.count(), 2)

    def test_restore_does_not_reattach_the_detached_contract(self):
        self.archive()
        self.restore()

        self.contract.refresh_from_db()
        self.assertIsNone(self.contract.purchase_order)

    def test_restore_leaves_an_individually_deleted_line_deleted(self):
        with self.tenant_context(self.tenant), acting_as(self.tenant_admin):
            self.second_line.delete()

        self.archive()
        self.restore()

        self.first_line.refresh_from_db()
        self.assertIsNone(self.first_line.deleted_at)
        self.second_line.refresh_from_db()
        self.assertIsNotNone(self.second_line.deleted_at)
        self.assertEqual(self.order.lines.count(), 1)

    def test_restore_refuses_when_another_active_order_uses_the_number(self):
        self.archive()
        self.make_order(order_number=self.order.order_number)

        with self.assertRaises(ArchiveBlocked) as caught:
            self.restore()

        self.assertIn(self.order.order_number, caught.exception.headline)
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.deleted_at)
        self.assertEqual(object_changes(self.order, action="update").count(), 0)
        for line in (self.first_line, self.second_line):
            line.refresh_from_db()
            self.assertIsNotNone(line.deleted_at)

    # ------------------------------------------------------------- atomicity
    def test_archive_is_atomic_when_a_later_step_fails(self):
        with patch("procurement.archive_services._detach_contracts", side_effect=RuntimeError("archive failed")):
            with self.assertRaises(RuntimeError):
                self.archive()

        self.order.refresh_from_db()
        self.assertIsNone(self.order.deleted_at)
        for line in (self.first_line, self.second_line):
            line.refresh_from_db()
            self.assertIsNone(line.deleted_at)
        self.assertEqual(object_changes(self.order).count(), 0)
        self.assertEqual(object_changes(self.first_line).count(), 0)

    # ------------------------------------------------------------- tenant boundary
    def test_archive_fails_closed_for_an_order_outside_the_active_tenant(self):
        other_tenant = Tenant.objects.create(name="P619 PO Other", slug="p619-po-other")
        other_location = Location.objects.create(
            name="P619 PO Other Loc", slug="p619-po-other-loc", site=self.site, tenant=other_tenant
        )
        foreign = self.make_order(order_number="PO-619-F", tenant=other_tenant, location=other_location)
        foreign_line = self.make_line(foreign)

        with self.tenant_context(self.tenant), acting_as(self.tenant_admin):
            with self.assertRaises(PermissionDenied):
                archive_purchase_order(foreign)

        foreign.refresh_from_db()
        foreign_line.refresh_from_db()
        self.assertIsNone(foreign.deleted_at)
        self.assertIsNone(foreign_line.deleted_at)


class PurchaseOrderArchiveSurfaceTests(PurchaseOrderArchiveFixtureMixin, TenantTestMixin, TestCase):
    """Every delete/restore surface reaches the aggregate service."""

    PO_PERMISSIONS = [
        "procurement.view_purchaseorder",
        "procurement.change_purchaseorder",
        "procurement.delete_purchaseorder",
        "procurement.view_purchaseorderline",
        "procurement.view_contract",
        "procurement.change_contract",
        "core.view_recyclebin",
        "core.change_recyclebin",
    ]

    def setUp(self):
        self.setup_tenant_context(
            name="P619 PO Surface Tenant", slug="p619-po-surface", permissions=list(self.PO_PERMISSIONS)
        )
        self.make_reference_data()
        self.order = self.make_order()
        self.line = self.make_line(self.order)
        self.contract = self.make_contract(self.order)
        self.client_login_to_tenant(self.tenant_user, self.tenant)

    def _messages(self, response):
        return [str(message) for message in get_messages(response.wsgi_request)]

    def _archive_row(self, order):
        with self.tenant_context(self.tenant), acting_as(self.tenant_admin):
            return archive_purchase_order(order)

    def _restore_url(self, order):
        content_type = ContentType.objects.get_for_model(PurchaseOrder)
        return reverse("object_restore", kwargs={"content_type_id": content_type.pk, "object_id": order.pk})

    # ------------------------------------------------------------- UI delete
    def test_ui_delete_archives_the_order_and_its_lines(self):
        response = self.client.post(reverse("procurement:purchaseorder_delete", kwargs={"pk": self.order.pk}))

        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(PurchaseOrder.all_objects.get(pk=self.order.pk).deleted_at)
        self.assertIsNotNone(PurchaseOrderLine.all_objects.get(pk=self.line.pk).deleted_at)
        self.contract.refresh_from_db()
        self.assertIsNone(self.contract.purchase_order)

    def test_ui_delete_with_a_live_link_is_refused_with_a_message(self):
        self.make_link(self.line)

        response = self.client.post(
            reverse("procurement:purchaseorder_delete", kwargs={"pk": self.order.pk}), follow=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("fulfillment link" in message for message in self._messages(response)))
        self.assertIsNone(PurchaseOrder.all_objects.get(pk=self.order.pk).deleted_at)
        self.assertIsNone(PurchaseOrderLine.all_objects.get(pk=self.line.pk).deleted_at)

    # ------------------------------------------------------------- API delete
    def _api_detail_url(self, order):
        return reverse("api:procurement_api:purchaseorder-detail", kwargs={"pk": order.pk})

    def test_api_delete_archives_the_order_and_its_lines(self):
        detail_url = self._api_detail_url(self.order)
        current = self.client.get(detail_url)

        response = self.client.delete(detail_url, HTTP_IF_MATCH=current["ETag"])

        self.assertEqual(response.status_code, 204)
        self.assertIsNotNone(PurchaseOrder.all_objects.get(pk=self.order.pk).deleted_at)
        self.assertIsNotNone(PurchaseOrderLine.all_objects.get(pk=self.line.pk).deleted_at)

    def test_api_delete_with_a_live_link_is_a_400_and_writes_nothing(self):
        self.make_link(self.line)
        detail_url = self._api_detail_url(self.order)
        current = self.client.get(detail_url)

        response = self.client.delete(detail_url, HTTP_IF_MATCH=current["ETag"])

        self.assertEqual(response.status_code, 400)
        self.assertIsNone(PurchaseOrder.all_objects.get(pk=self.order.pk).deleted_at)
        self.assertIsNone(PurchaseOrderLine.all_objects.get(pk=self.line.pk).deleted_at)

    def test_api_delete_of_another_tenants_order_is_404(self):
        other = Tenant.objects.create(name="P619 PO API Other", slug="p619-po-api-other")
        other_location = Location.objects.create(
            name="P619 PO API Other Loc", slug="p619-po-api-other-loc", site=self.site, tenant=other
        )
        foreign = self.make_order(order_number="PO-619-F", tenant=other, location=other_location)

        response = self.client.delete(self._api_detail_url(foreign))

        self.assertEqual(response.status_code, 404)
        self.assertIsNone(PurchaseOrder.all_objects.get(pk=foreign.pk).deleted_at)

    # ------------------------------------------------------------- recycle bin
    def test_recycle_bin_restore_uses_the_service(self):
        self._archive_row(self.order)

        response = self.client.post(self._restore_url(self.order))

        self.assertEqual(response.status_code, 302)
        self.assertIsNone(PurchaseOrder.all_objects.get(pk=self.order.pk).deleted_at)
        self.assertIsNone(PurchaseOrderLine.all_objects.get(pk=self.line.pk).deleted_at)

    def test_recycle_bin_restore_refusal_keeps_everything_archived(self):
        self._archive_row(self.order)
        self.make_order(order_number=self.order.order_number)

        response = self.client.post(self._restore_url(self.order), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("another active purchase order" in message for message in self._messages(response)))
        self.assertIsNotNone(PurchaseOrder.all_objects.get(pk=self.order.pk).deleted_at)
        self.assertIsNotNone(PurchaseOrderLine.all_objects.get(pk=self.line.pk).deleted_at)
