import json

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.test import TestCase, override_settings

from assets.choices import RequestStatusChoices
from assets.models import AssetRequest, AssetType, Manufacturer, StatusLabel, Supplier
from core.currency import CURRENCY_CHOICES
from core.models import ObjectChange
from core.tasks.context import TaskContext
from inventory.models import Accessory, AccessoryStock, Consumable, ConsumableStock
from licenses.models import License
from organization.models import Location, Site, Tenant
from procurement.forms import PurchaseOrderForm, PurchaseOrderLineForm
from procurement.models import FulfillmentLink, PurchaseOrder, PurchaseOrderLine
from software.models import Software

User = get_user_model()


def _expected(*lines):
    """Recorded receipt quantities a receipt submission is prepared against."""
    expected = {}
    for line in lines:
        line.refresh_from_db()
        expected[line.pk] = line.qty_received
    return expected


class ProcurementStatusTransitionTests(TestCase):
    def setUp(self):
        # Create user
        self.user = User.objects.create_superuser(username="testuser", email="test@example.com", password="password")

        # receive_purchase_order() unconditionally requires a 'deployable' StatusLabel
        # to exist (it's looked up before branching on asset_type/component/license/etc.),
        # so every test in this class that calls it — including license-only lines —
        # needs one available.
        StatusLabel.objects.get_or_create(name="Deployable", defaults={"type": "deployable", "slug": "deployable"})

        # Create a site and location
        # ADR-0001 phase 4: stock (ComponentStock/AccessoryStock/ConsumableStock)
        # requires a location owned by a tenant.
        self.tenant = Tenant.objects.create(name="Procurement Test Tenant", slug="procurement-test-tenant")
        self.site = Site.objects.create(name="Test Site", slug="test-site")
        self.location = Location.objects.create(
            name="Test Location", slug="test-location", site=self.site, tenant=self.tenant
        )

        # Create a supplier
        self.supplier = Supplier.objects.create(name="Test Supplier", slug="test-supplier")

        # Create manufacturer and asset type for lines
        self.manufacturer = Manufacturer.objects.create(name="Test Manufacturer", slug="test-manufacturer")
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer, model="Test Model", slug="test-model", requestable=True
        )

        # Create software and license
        self.software = Software.objects.create(name="Test Software Product", manufacturer=self.manufacturer)
        self.license = License.objects.create(name="Test License Product", software=self.software, seats=10)

        # Create purchase order (starts in draft status)
        self.po = PurchaseOrder.objects.create(
            order_number="PO-1001", supplier=self.supplier, destination_location=self.location, created_by=self.user
        )

    def test_approve_purchase_order_successful(self):
        # Must have at least one line item to approve
        PurchaseOrderLine.objects.create(
            purchase_order=self.po, asset_type=self.asset_type, qty_ordered=5, unit_price=10.00
        )
        from procurement.services import approve_purchase_order

        approve_purchase_order(self.po)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.STATUS_APPROVED)

    @override_settings(
        ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS=None,
        REQUISITION_AUTO_APPROVAL_THRESHOLDS=None,
    )
    def test_link_asset_request_to_purchase_order_creates_the_owned_fulfillment_graph(self):
        from procurement import services

        link_request = getattr(services, "link_asset_request_to_purchase_order", None)
        self.assertTrue(callable(link_request), "Asset Request procurement service is missing")
        self.po.tenant = self.tenant
        self.po.save(update_fields=["tenant"])
        request = AssetRequest.objects.create(
            tenant=self.tenant,
            requester=self.user,
            asset_type=self.asset_type,
            qty=1,
            status=RequestStatusChoices.APPROVED,
        )

        link = link_request(self.po, request.pk, user=self.user)

        request.refresh_from_db()
        self.assertEqual(request.status, RequestStatusChoices.PROCUREMENT)
        self.assertEqual(link.tenant, self.tenant)
        self.assertEqual(link.asset_request, request)
        self.assertEqual(link.qty_allocated, 1)
        self.assertEqual(link.qty_received, 0)
        self.assertEqual(link.purchase_order_line.tenant, self.tenant)
        self.assertEqual(link.purchase_order_line.purchase_order, self.po)
        self.assertEqual(link.purchase_order_line.asset_type, self.asset_type)
        self.assertEqual(link.purchase_order_line.qty_ordered, 1)

    def test_link_refuses_multi_unit_serialised_requests(self):
        from procurement.services import link_asset_request_to_purchase_order

        self.po.tenant = self.tenant
        self.po.save(update_fields=["tenant"])
        request = AssetRequest.objects.create(
            tenant=self.tenant,
            requester=self.user,
            asset_type=self.asset_type,
            qty=2,
            status=RequestStatusChoices.APPROVED,
        )

        with self.assertRaisesMessage(ValidationError, "more than one serialised unit"):
            link_asset_request_to_purchase_order(self.po, request.pk, user=self.user)

        request.refresh_from_db()
        self.assertEqual(request.status, RequestStatusChoices.APPROVED)
        self.assertFalse(FulfillmentLink.objects.filter(asset_request=request).exists())
        self.assertFalse(PurchaseOrderLine.objects.filter(purchase_order=self.po).exists())

    def test_link_asset_request_to_purchase_order_is_idempotent(self):
        from procurement.services import link_asset_request_to_purchase_order

        self.po.tenant = self.tenant
        self.po.save(update_fields=["tenant"])
        request = AssetRequest.objects.create(
            tenant=self.tenant,
            requester=self.user,
            asset_type=self.asset_type,
            qty=1,
            status=RequestStatusChoices.APPROVED,
        )

        first = link_asset_request_to_purchase_order(self.po, request.pk, user=self.user)
        second = link_asset_request_to_purchase_order(self.po, request.pk, user=self.user)

        self.assertEqual(second.pk, first.pk)
        self.assertEqual(FulfillmentLink.objects.filter(asset_request=request).count(), 1)
        self.assertEqual(PurchaseOrderLine.objects.filter(purchase_order=self.po).count(), 1)

    def test_multi_unit_asset_type_group_links_and_receives_each_child_idempotently(self):
        from procurement.services import (
            approve_purchase_order,
            link_asset_request_to_purchase_order,
            order_purchase_order,
            receive_purchase_order,
        )

        self.po.tenant = self.tenant
        self.po.save(update_fields=["tenant"])
        parent = AssetRequest.objects.create(
            tenant=self.tenant,
            requester=self.user,
            asset_type=self.asset_type,
            qty=2,
            is_group=True,
            status=RequestStatusChoices.APPROVED,
        )
        children = []
        for _ in range(2):
            child = AssetRequest(
                tenant=self.tenant,
                requester=self.user,
                asset_type=self.asset_type,
                qty=1,
                parent=parent,
                status=RequestStatusChoices.APPROVED,
            )
            child._skip_duplicate_check = True
            child.save()
            children.append(child)

        from django.urls import reverse

        self.client.force_login(self.user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant.pk
        session.save()
        child_response = self.client.get(reverse("assets:request_detail", kwargs={"pk": children[0].pk}))
        self.assertNotContains(child_response, "Create Purchase Order")

        with self.assertRaisesMessage(ValidationError, "group parent"):
            link_asset_request_to_purchase_order(self.po, children[0].pk, user=self.user)

        first_link = link_asset_request_to_purchase_order(self.po, parent.pk, user=self.user)
        second_link = link_asset_request_to_purchase_order(self.po, parent.pk, user=self.user)

        line = first_link.purchase_order_line
        self.assertEqual(second_link.pk, first_link.pk)
        self.assertEqual(line.qty_ordered, 2)
        self.assertSetEqual(
            set(FulfillmentLink.objects.filter(purchase_order_line=line).values_list("asset_request_id", flat=True)),
            {child.pk for child in children},
        )
        parent.refresh_from_db()
        self.assertEqual(parent.status, RequestStatusChoices.PROCUREMENT)
        self.assertFalse(parent.fulfillment_links.exists())
        for child in children:
            child.refresh_from_db()
            self.assertEqual(child.status, RequestStatusChoices.PROCUREMENT)

        detail_response = self.client.get(reverse("assets:request_detail", kwargs={"pk": parent.pk}))
        self.assertContains(detail_response, self.po.order_number)

        approve_purchase_order(self.po)
        order_purchase_order(self.po)
        receive_purchase_order(
            self.po,
            {line.pk: 1},
            [{"line_id": line.pk, "serial_number": "GROUP-SN-1", "asset_tag": "GROUP-TAG-1"}],
            expected_received=_expected(line),
        )

        parent.refresh_from_db()
        for child in children:
            child.refresh_from_db()
        self.assertEqual(parent.status, RequestStatusChoices.PROCUREMENT)
        self.assertEqual(
            [child.status for child in children].count(RequestStatusChoices.APPROVED),
            1,
        )
        self.assertEqual(
            [child.status for child in children].count(RequestStatusChoices.PROCUREMENT),
            1,
        )
        first_link = FulfillmentLink.objects.get(asset_request=children[0])
        second_link = FulfillmentLink.objects.get(asset_request=children[1])
        self.assertEqual((first_link.qty_received, first_link.qty_allocated), (1, 1))
        self.assertEqual((second_link.qty_received, second_link.qty_allocated), (0, 1))
        self.assertFalse(second_link.fully_delivered)

        receive_purchase_order(
            self.po,
            {line.pk: 1},
            [{"line_id": line.pk, "serial_number": "GROUP-SN-2", "asset_tag": "GROUP-TAG-2"}],
            expected_received=_expected(line),
        )

        parent.refresh_from_db()
        self.assertEqual(parent.status, RequestStatusChoices.APPROVED)
        for child in children:
            child.refresh_from_db()
            self.assertEqual(child.status, RequestStatusChoices.APPROVED)
            self.assertIsNotNone(child.asset_id)
            link = FulfillmentLink.objects.get(asset_request=child)
            self.assertEqual(link.qty_received, 1)
            self.assertTrue(link.fully_delivered)

    def test_cancelling_group_purchase_order_reverts_parent_and_children(self):
        from procurement.services import cancel_purchase_order, link_asset_request_to_purchase_order

        self.po.tenant = self.tenant
        self.po.save(update_fields=["tenant"])
        parent = AssetRequest.objects.create(
            tenant=self.tenant,
            requester=self.user,
            asset_type=self.asset_type,
            qty=2,
            is_group=True,
            status=RequestStatusChoices.APPROVED,
        )
        children = []
        for _ in range(2):
            child = AssetRequest(
                tenant=self.tenant,
                requester=self.user,
                asset_type=self.asset_type,
                qty=1,
                parent=parent,
                status=RequestStatusChoices.APPROVED,
            )
            child._skip_duplicate_check = True
            child.save()
            children.append(child)
        link_asset_request_to_purchase_order(self.po, parent.pk, user=self.user)

        cancel_purchase_order(self.po)

        parent.refresh_from_db()
        self.assertEqual(parent.status, RequestStatusChoices.APPROVED)
        for child in children:
            child.refresh_from_db()
            self.assertEqual(child.status, RequestStatusChoices.APPROVED)
            self.assertFalse(child.fulfillment_links.exists())

    @override_settings(
        ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS=None,
        REQUISITION_AUTO_APPROVAL_THRESHOLDS=None,
    )
    def test_link_without_any_threshold_configuration_still_reserves_and_links(self):
        from procurement.services import link_asset_request_to_purchase_order

        self.po.tenant = self.tenant
        self.po.save(update_fields=["tenant"])
        request = AssetRequest.objects.create(
            tenant=self.tenant,
            requester=self.user,
            asset_type=self.asset_type,
            status=RequestStatusChoices.APPROVED,
        )

        link = link_asset_request_to_purchase_order(self.po, request.pk, user=self.user)

        request.refresh_from_db()
        self.assertEqual(request.status, RequestStatusChoices.PROCUREMENT)
        self.assertTrue(FulfillmentLink.objects.filter(pk=link.pk, asset_request=request).exists())
        self.assertTrue(PurchaseOrderLine.objects.filter(purchase_order=self.po).exists())

    def test_link_asset_request_to_purchase_order_rejects_foreign_request_id(self):
        from procurement.services import link_asset_request_to_purchase_order

        self.po.tenant = self.tenant
        self.po.save(update_fields=["tenant"])
        other_tenant = Tenant.objects.create(name="Foreign Request Tenant", slug="foreign-request-tenant")
        foreign_request = AssetRequest.objects.create(
            tenant=other_tenant,
            requester=self.user,
            asset_type=self.asset_type,
            status=RequestStatusChoices.APPROVED,
        )

        with self.assertRaisesMessage(ValidationError, "does not exist"):
            link_asset_request_to_purchase_order(self.po, foreign_request.pk, user=self.user)

        foreign_request.refresh_from_db()
        self.assertEqual(foreign_request.status, RequestStatusChoices.APPROVED)
        self.assertFalse(FulfillmentLink.objects.filter(asset_request=foreign_request).exists())
        self.assertFalse(PurchaseOrderLine.objects.filter(purchase_order=self.po).exists())

    def test_link_asset_request_to_purchase_order_rejects_tenantless_legacy_rows(self):
        from procurement.services import link_asset_request_to_purchase_order

        tenantless_request = AssetRequest.objects.create(
            requester=self.user,
            asset_type=self.asset_type,
            status=RequestStatusChoices.APPROVED,
        )

        with self.assertRaisesMessage(ValidationError, "tenant-owned"):
            link_asset_request_to_purchase_order(self.po, tenantless_request.pk, user=self.user)

        tenantless_request.refresh_from_db()
        self.assertEqual(tenantless_request.status, RequestStatusChoices.APPROVED)
        self.assertFalse(FulfillmentLink.objects.filter(asset_request=tenantless_request).exists())
        self.assertFalse(PurchaseOrderLine.objects.filter(purchase_order=self.po).exists())

    def test_link_asset_request_to_purchase_order_rejects_malformed_request_id(self):
        from procurement.services import link_asset_request_to_purchase_order

        self.po.tenant = self.tenant
        self.po.save(update_fields=["tenant"])

        with self.assertRaisesMessage(ValidationError, "Invalid Asset Request identifier"):
            link_asset_request_to_purchase_order(self.po, "not-an-id", user=self.user)

        self.assertFalse(PurchaseOrderLine.objects.filter(purchase_order=self.po).exists())

    def test_fulfillment_link_unique_constraint_only_covers_active_rows(self):
        constraint = next(c for c in FulfillmentLink._meta.constraints if c.name == "unique_request_po_line_link")
        self.assertEqual(constraint.condition, models.Q(deleted_at__isnull=True))

    def test_fulfillment_link_rejects_cross_tenant_request_and_line(self):
        other_tenant = Tenant.objects.create(name="Other Procurement Tenant", slug="other-procurement-tenant")
        other_location = Location.objects.create(
            tenant=other_tenant,
            site=self.site,
            name="Other Location",
            slug="other-location",
        )
        other_po = PurchaseOrder.objects.create(
            tenant=other_tenant,
            order_number="PO-OTHER",
            supplier=self.supplier,
            destination_location=other_location,
            created_by=self.user,
        )
        other_line = PurchaseOrderLine.objects.create(
            tenant=other_tenant,
            purchase_order=other_po,
            asset_type=self.asset_type,
            qty_ordered=1,
        )
        request = AssetRequest.objects.create(
            tenant=self.tenant,
            requester=self.user,
            asset_type=self.asset_type,
            status=RequestStatusChoices.APPROVED,
        )
        link = FulfillmentLink(
            tenant=self.tenant,
            asset_request=request,
            purchase_order_line=other_line,
            qty_allocated=1,
        )

        with self.assertRaisesMessage(ValidationError, "same tenant"):
            link.full_clean()

    def test_approve_purchase_order_no_lines_fails(self):
        from procurement.services import approve_purchase_order

        with self.assertRaises(ValidationError):
            approve_purchase_order(self.po)

    def test_approve_purchase_order_rejects_stale_deleted_instance(self):
        from procurement.services import approve_purchase_order

        PurchaseOrderLine.objects.create(
            purchase_order=self.po, asset_type=self.asset_type, qty_ordered=1, unit_price=10.00
        )
        # A soft delete cascades audit entries for the collected children, which
        # the change-logging layer only tolerates inside an execution context;
        # mirror the request scope a production delete always runs in.
        with TaskContext(operation="procurement.test_legacy.stale_delete"):
            self.po.delete()

        with self.assertRaisesMessage(ValidationError, "Purchase order no longer exists"):
            approve_purchase_order(self.po)

    def test_order_purchase_order_successful(self):
        # Add line, approve first
        PurchaseOrderLine.objects.create(
            purchase_order=self.po, asset_type=self.asset_type, qty_ordered=5, unit_price=10.00
        )
        from procurement.services import approve_purchase_order, order_purchase_order

        approve_purchase_order(self.po)
        order_purchase_order(self.po)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.STATUS_ORDERED)
        self.assertIsNotNone(self.po.order_date)

    def test_order_purchase_order_without_approval_fails(self):
        from procurement.services import order_purchase_order

        with self.assertRaises(ValidationError):
            order_purchase_order(self.po)

    def test_cancel_purchase_order_reverts_linked_requests(self):
        self.po.tenant = self.tenant
        self.po.save(update_fields=["tenant"])
        # Create a PO line
        line = PurchaseOrderLine.objects.create(
            tenant=self.tenant,
            purchase_order=self.po,
            asset_type=self.asset_type,
            qty_ordered=2,
            unit_price=100.00,
        )
        # Create an asset request and fulfillment link
        request = AssetRequest.objects.create(
            tenant=self.tenant,
            requester=self.user,
            asset_type=self.asset_type,
            qty=2,
            status=RequestStatusChoices.PROCUREMENT,
        )
        link = FulfillmentLink.objects.create(
            tenant=self.tenant,
            asset_request=request,
            purchase_order_line=line,
            qty_allocated=2,
        )
        line_id = line.pk
        link_id = link.pk

        # Cancel the PO
        from procurement.services import cancel_purchase_order

        cancel_purchase_order(self.po)

        self.po.refresh_from_db()
        request.refresh_from_db()

        self.assertEqual(self.po.status, PurchaseOrder.STATUS_CANCELLED)
        self.assertEqual(request.status, RequestStatusChoices.APPROVED)
        self.assertFalse(FulfillmentLink.objects.filter(purchase_order_line=line).exists())
        for model, object_id in ((PurchaseOrderLine, line_id), (FulfillmentLink, link_id)):
            with self.subTest(model=model.__name__):
                self.assertTrue(
                    ObjectChange._base_manager.filter(
                        changed_object_type=ContentType.objects.get_for_model(model),
                        changed_object_id=object_id,
                        action="delete",
                        request_id__isnull=False,
                    ).exists(),
                    f"{model.__name__} cascade delete was not attributed to an execution context",
                )

    def test_reopen_cancelled_purchase_order(self):
        from procurement.services import cancel_purchase_order, reopen_purchase_order

        cancel_purchase_order(self.po)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.STATUS_CANCELLED)

        reopen_purchase_order(self.po)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.STATUS_DRAFT)

    def test_license_purchase_order_line_creation_and_validation(self):
        # Successful creation of license line item
        line = PurchaseOrderLine.objects.create(
            purchase_order=self.po, license=self.license, qty_ordered=3, unit_price=50.00
        )
        self.assertEqual(line.license, self.license)
        self.assertEqual(str(line), f"3x {self.license} for PO PO-1001")

        # Validation fails when both license and asset_type are specified
        line.asset_type = self.asset_type
        with self.assertRaises(ValidationError):
            line.clean()

        # Validation fails when nothing is specified
        line.asset_type = None
        line.license = None
        with self.assertRaises(ValidationError):
            line.clean()

    def test_receive_license_line_increases_qty_received(self):
        line = PurchaseOrderLine.objects.create(
            purchase_order=self.po, license=self.license, qty_ordered=5, unit_price=50.00
        )
        from procurement.services import approve_purchase_order, order_purchase_order, receive_purchase_order

        approve_purchase_order(self.po)
        order_purchase_order(self.po)

        # Receive 3 licenses
        receive_purchase_order(self.po, {line.pk: 3}, expected_received=_expected(line))

        line.refresh_from_db()
        self.assertEqual(line.qty_received, 3)
        self.assertEqual(line.qty_outstanding, 2)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.STATUS_PARTIAL)

        # Receive the remaining 2 licenses
        receive_purchase_order(self.po, {line.pk: 2}, expected_received=_expected(line))
        line.refresh_from_db()
        self.assertEqual(line.qty_received, 5)
        self.assertEqual(line.qty_outstanding, 0)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.STATUS_RECEIVED)

    def test_receive_license_line_does_not_grow_seat_pool(self):
        """WS2-9: receiving a license PO line must NOT increment License.seats.

        License.seats is a manually-entered entitlement, not a quantity materialised
        from receipts; receiving a license line only records progress on the line."""
        self.po.tenant = self.tenant
        self.po.save(update_fields=["tenant"])
        line = PurchaseOrderLine.objects.create(
            tenant=self.tenant,
            purchase_order=self.po,
            license=self.license,
            qty_ordered=5,
            unit_price=50.00,
        )
        seats_before = self.license.seats

        from procurement.services import (
            approve_purchase_order,
            order_purchase_order,
            receive_purchase_order,
        )

        approve_purchase_order(self.po)
        order_purchase_order(self.po)
        receive_purchase_order(self.po, {line.pk: 5}, expected_received=_expected(line))

        # Seat pool is entitlement-driven: unchanged by receipt.
        self.license.refresh_from_db()
        self.assertEqual(
            self.license.seats,
            seats_before,
            "Receiving a license PO line must not auto-grow License.seats",
        )
        line.refresh_from_db()
        self.assertEqual(line.qty_received, 5)

    def test_receive_component_locks_stock_rows_in_global_item_order(self):
        """Stock locks are acquired by item id, not by PO-line order."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from assets.models import Category, StatusLabel
        from inventory.models import Component, ComponentStock
        from procurement.services import (
            approve_purchase_order,
            order_purchase_order,
            receive_purchase_order,
        )

        StatusLabel.objects.get_or_create(name="Deployable", defaults={"type": "deployable", "slug": "deployable"})
        category = Category.objects.create(name="Comp Cat", slug="comp-cat", applies_to={"component": True})
        lower_component = Component.objects.create(name="RAM", manufacturer=self.manufacturer, category=category)
        higher_component = Component.objects.create(name="SSD", manufacturer=self.manufacturer, category=category)
        higher_line = PurchaseOrderLine.objects.create(
            purchase_order=self.po,
            component=higher_component,
            qty_ordered=10,
            unit_price=5.00,
        )
        lower_line = PurchaseOrderLine.objects.create(
            purchase_order=self.po,
            component=lower_component,
            qty_ordered=10,
            unit_price=5.00,
        )
        approve_purchase_order(self.po)
        order_purchase_order(self.po)

        with CaptureQueriesContext(connection) as ctx:
            receive_purchase_order(
                self.po,
                {higher_line.pk: 5, lower_line.pk: 3},
                expected_received=_expected(higher_line, lower_line),
            )

        lower_stock = ComponentStock.objects.get(component=lower_component, location=self.location)
        higher_stock = ComponentStock.objects.get(component=higher_component, location=self.location)
        self.assertEqual(lower_stock.qty, 3)
        self.assertEqual(higher_stock.qty, 5)

        lock_queries = [
            q["sql"].lower()
            for q in ctx.captured_queries
            if "componentstock" in q["sql"].lower() and "for update" in q["sql"].lower()
        ]
        self.assertEqual(len(lock_queries), 1, lock_queries)
        self.assertIn("order by", lock_queries[0])
        self.assertIn("component_id", lock_queries[0])

    def test_receive_accessory_and_consumable_updates_locked_stock(self):
        from procurement.services import approve_purchase_order, order_purchase_order, receive_purchase_order

        accessory = Accessory.objects.create(name="Dock", manufacturer=self.manufacturer)
        consumable = Consumable.objects.create(name="Cable ties", manufacturer=self.manufacturer)
        accessory_line = PurchaseOrderLine.objects.create(
            purchase_order=self.po,
            accessory=accessory,
            qty_ordered=4,
            unit_price=20.00,
        )
        consumable_line = PurchaseOrderLine.objects.create(
            purchase_order=self.po,
            consumable=consumable,
            qty_ordered=10,
            unit_price=1.00,
        )
        approve_purchase_order(self.po)
        order_purchase_order(self.po)

        receive_purchase_order(
            self.po,
            {accessory_line.pk: 3, consumable_line.pk: 7},
            expected_received=_expected(accessory_line, consumable_line),
        )

        self.assertEqual(AccessoryStock.objects.get(accessory=accessory, location=self.location).qty, 3)
        self.assertEqual(ConsumableStock.objects.get(consumable=consumable, location=self.location).qty, 7)

    def test_receiving_status_guards(self):
        line = PurchaseOrderLine.objects.create(
            purchase_order=self.po, asset_type=self.asset_type, qty_ordered=5, unit_price=10.00
        )
        from procurement.services import receive_purchase_order

        # Try to receive while in Draft
        with self.assertRaises(ValidationError):
            receive_purchase_order(self.po, {line.pk: 2}, expected_received=_expected(line))

    def test_receive_form_view_post_does_not_crash(self):
        line = PurchaseOrderLine.objects.create(
            purchase_order=self.po, asset_type=self.asset_type, qty_ordered=5, unit_price=10.00
        )
        from procurement.services import approve_purchase_order, order_purchase_order

        approve_purchase_order(self.po)
        order_purchase_order(self.po)

        self.client.force_login(self.user)
        response = self.client.post(
            f"/procurement/orders/{self.po.pk}/receive/",
            {
                "form-TOTAL_FORMS": "1",
                "form-INITIAL_FORMS": "1",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-line_id": line.pk,
                "form-0-qty_to_receive": 2,
                "step": "1",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "procurement/purchaseorder_receive_step2.html")

    def test_receive_form_view_step2_submit_empty_details_does_not_crash(self):
        line = PurchaseOrderLine.objects.create(
            purchase_order=self.po, asset_type=self.asset_type, qty_ordered=5, unit_price=10.00
        )
        from procurement.services import approve_purchase_order, order_purchase_order

        approve_purchase_order(self.po)
        order_purchase_order(self.po)

        # Setup session quantities for step 2; the receipt-state snapshot travels with the
        # step-2 submission itself.
        session = self.client.session
        session["receive_po_quantities"] = {line.pk: 2}
        session.save()

        # Deployable status label is required by the receiving service
        from assets.models import StatusLabel

        StatusLabel.objects.get_or_create(name="Deployable", type="deployable", slug="deployable")

        self.client.force_login(self.user)
        response = self.client.post(
            f"/procurement/orders/{self.po.pk}/receive/",
            {
                "form-TOTAL_FORMS": "2",
                "form-INITIAL_FORMS": "2",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                # Form 0: filled out
                "form-0-line_id": line.pk,
                "form-0-serial_number": "SN123",
                "form-0-asset_tag": "TAG123",
                "form-0-name": "Test Asset 1",
                # Form 1: only line_id is submitted (simulating blank user input)
                "form-1-line_id": line.pk,
                "step": "2",
                "expected_received": json.dumps({str(line.pk): 0}),
            },
        )
        # It should redirect to absolute URL on success
        self.assertEqual(response.status_code, 302)

    def test_receive_po_with_blank_serial_stores_empty_string(self):
        """Regression: blank serial_number must store '' not NULL (IntegrityError guard)."""
        from assets.models import StatusLabel

        StatusLabel.objects.get_or_create(name="Deployable", type="deployable", slug="deployable")

        line = PurchaseOrderLine.objects.create(
            purchase_order=self.po, asset_type=self.asset_type, qty_ordered=1, unit_price=99.00
        )
        from procurement.services import approve_purchase_order, order_purchase_order, receive_purchase_order

        approve_purchase_order(self.po)
        order_purchase_order(self.po)

        # Blank serial — the form allows "Optional" and must not IntegrityError.
        receive_purchase_order(
            self.po,
            {line.pk: 1},
            asset_details=[{"line_id": line.pk, "serial_number": "", "asset_tag": "", "name": "Test Asset"}],
            expected_received=_expected(line),
        )

        from assets.models import Asset

        asset = Asset.objects.get(purchase_order_line=line)
        self.assertEqual(asset.serial_number, "", "Blank serial should be stored as '' not NULL")

    def test_line_edit_view_get_returns_editing_line_id(self):
        line = PurchaseOrderLine.objects.create(
            purchase_order=self.po, asset_type=self.asset_type, qty_ordered=5, unit_price=10.00
        )
        self.client.force_login(self.user)
        response = self.client.get(f"/procurement/lines/{line.pk}/edit/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="qty_ordered"')
        self.assertContains(response, 'name="unit_price"')

    def test_line_edit_view_post_updates_price_and_qty(self):
        line = PurchaseOrderLine.objects.create(
            purchase_order=self.po, asset_type=self.asset_type, qty_ordered=5, unit_price=10.00
        )
        self.client.force_login(self.user)
        response = self.client.post(f"/procurement/lines/{line.pk}/edit/", {"qty_ordered": 12, "unit_price": "15.50"})
        self.assertEqual(response.status_code, 200)
        line.refresh_from_db()
        self.assertEqual(line.qty_ordered, 12)
        self.assertEqual(float(line.unit_price), 15.50)


class PurchaseOrderFormTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name="Test Site", slug="test-site")
        self.location = Location.objects.create(name="Test Location", slug="test-location", site=self.site)
        self.supplier = Supplier.objects.create(name="Test Supplier", slug="test-supplier")
        self.tenant = Tenant.objects.create(name="Test Tenant", slug="test-tenant")

    def test_po_form_has_tenant_field(self):
        form = PurchaseOrderForm()
        self.assertIn("tenant", form.fields)

    def test_po_form_does_not_expose_lifecycle_status(self):
        form = PurchaseOrderForm()
        self.assertNotIn("status", form.fields)

    def test_po_form_saves_tenant(self):
        form_data = {
            "order_number": "PO-TEST-123",
            "supplier": self.supplier.pk,
            "order_date": "2026-06-07",
            "expected_delivery_date": "2026-06-14",
            "destination_location": self.location.pk,
            "tenant": self.tenant.pk,
            "notes": "Test notes",
        }
        form = PurchaseOrderForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        po = form.save()
        self.assertEqual(po.tenant, self.tenant)


class PurchaseOrderLineFormTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name="Test Manufacturer", slug="test-manufacturer")
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer, model="Test Model", slug="test-model", requestable=True
        )
        self.software = Software.objects.create(name="Test Software Product", manufacturer=self.manufacturer)
        self.license = License.objects.create(name="Test License Product", software=self.software, seats=10)

    def test_form_validation_valid_asset_type(self):
        form_data = {
            "item_category": "asset_type",
            "asset_type": self.asset_type.pk,
            "qty_ordered": 3,
            "unit_price": 15.00,
        }
        form = PurchaseOrderLineForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        cleaned_data = form.cleaned_data
        self.assertEqual(cleaned_data["asset_type"], self.asset_type)
        # Ensure other fields are cleared
        self.assertIsNone(cleaned_data["license"])
        self.assertIsNone(cleaned_data["component"])

    def test_form_validation_valid_license(self):
        form_data = {"item_category": "license", "license": self.license.pk, "qty_ordered": 2, "unit_price": 100.00}
        form = PurchaseOrderLineForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        cleaned_data = form.cleaned_data
        self.assertEqual(cleaned_data["license"], self.license)
        # Ensure other fields are cleared
        self.assertIsNone(cleaned_data["asset_type"])

    def test_form_validation_missing_category_fails(self):
        form_data = {"asset_type": self.asset_type.pk, "qty_ordered": 3, "unit_price": 15.00}
        form = PurchaseOrderLineForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn("__all__", form.errors)
        self.assertIn("Please select an Item Category.", form.errors["__all__"])

    def test_form_validation_missing_item_field_fails(self):
        form_data = {"item_category": "asset_type", "qty_ordered": 3, "unit_price": 15.00}
        form = PurchaseOrderLineForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn("asset_type", form.errors)
        self.assertIn("Please select a Asset Type.", form.errors["asset_type"])


class PurchaseOrderCurrencyTests(TestCase):
    """Tests for the per-PO currency field and the PurchaseOrderLine.currency property."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="currencyuser", email="currency@example.com", password="password"
        )
        self.site = Site.objects.create(name="Currency Site", slug="currency-site")
        self.location = Location.objects.create(name="Currency Location", slug="currency-location", site=self.site)
        self.supplier = Supplier.objects.create(name="Currency Supplier", slug="currency-supplier")
        self.manufacturer = Manufacturer.objects.create(name="Currency Manufacturer", slug="currency-manufacturer")
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="Currency Model",
            slug="currency-model",
            requestable=False,
        )

    def _make_po(self, currency=""):
        return PurchaseOrder.objects.create(
            order_number=f"PO-CUR-{currency or 'blank'}",
            supplier=self.supplier,
            destination_location=self.location,
            created_by=self.user,
            currency=currency,
        )

    # --- PurchaseOrder.currency field ---

    def test_currency_defaults_to_blank(self):
        """A new PO should default to blank (inherit tenant currency at display time)."""
        po = self._make_po()
        self.assertEqual(po.currency, "")

    def test_currency_explicit_value_stored(self):
        """An explicit ISO currency code must round-trip through the DB."""
        po = self._make_po(currency="USD")
        po.refresh_from_db()
        self.assertEqual(po.currency, "USD")

    def test_currency_choices_are_valid(self):
        """All CURRENCY_CHOICES codes must be accepted by the field."""
        valid_codes = [code for code, _ in CURRENCY_CHOICES]
        for code in valid_codes:
            po = PurchaseOrder(
                order_number=f"PO-CHK-{code}",
                supplier=self.supplier,
                destination_location=self.location,
                created_by=self.user,
                currency=code,
            )
            # full_clean validates choices; this must not raise
            po.full_clean()

    def test_currency_field_in_form(self):
        """PurchaseOrderForm must expose the currency field."""
        form = PurchaseOrderForm()
        self.assertIn("currency", form.fields)

    def test_form_saves_currency(self):
        """PurchaseOrderForm must persist an explicit currency on save."""
        tenant = Tenant.objects.create(name="Curr Tenant", slug="curr-tenant")
        form_data = {
            "order_number": "PO-FORM-GBP",
            "supplier": self.supplier.pk,
            "currency": "GBP",
            "order_date": "2026-06-14",
            "expected_delivery_date": "2026-07-01",
            "destination_location": self.location.pk,
            "tenant": tenant.pk,
            "notes": "",
        }
        form = PurchaseOrderForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        po = form.save()
        self.assertEqual(po.currency, "GBP")

    def test_form_saves_blank_currency(self):
        """An empty currency selection must save as blank (tenant-fallback semantics)."""
        tenant = Tenant.objects.create(name="Curr Tenant 2", slug="curr-tenant-2")
        form_data = {
            "order_number": "PO-FORM-BLANK",
            "supplier": self.supplier.pk,
            "currency": "",
            "order_date": "2026-06-14",
            "expected_delivery_date": "2026-07-01",
            "destination_location": self.location.pk,
            "tenant": tenant.pk,
            "notes": "",
        }
        form = PurchaseOrderForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        po = form.save()
        self.assertEqual(po.currency, "")

    # --- PurchaseOrderLine.currency property ---

    def test_line_currency_delegates_to_po(self):
        """PurchaseOrderLine.currency must return the parent PO's currency."""
        po = self._make_po(currency="EUR")
        line = PurchaseOrderLine.objects.create(
            purchase_order=po,
            asset_type=self.asset_type,
            qty_ordered=2,
            unit_price="99.99",
        )
        self.assertEqual(line.currency, "EUR")

    def test_line_currency_blank_when_po_currency_blank(self):
        """When the PO has no explicit currency, the line property also returns blank."""
        po = self._make_po(currency="")
        line = PurchaseOrderLine.objects.create(
            purchase_order=po,
            asset_type=self.asset_type,
            qty_ordered=1,
            unit_price="10.00",
        )
        self.assertEqual(line.currency, "")
