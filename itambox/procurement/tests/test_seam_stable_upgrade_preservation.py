"""Upgrade preservation for the Asset Request Procurement Seam Stable promotion.

The promotion adds one nullable column (``FulfillmentLink.qty_received``) and
must not rewrite, reinterpret, or re-deliver anything an operator recorded on
the supported Beta (including ``v1.0.0-beta.3``): existing fulfilment links
keep their purchase orders and reservations, requests recorded in
``procurement`` stay there, and no received quantities are invented for
pre-upgrade receipts. Untracked links simply complete on their next full
receipt.
"""

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from django.utils import timezone

from assets.choices import RequestStatusChoices
from assets.models import Asset, AssetRequest, AssetType, Manufacturer, StatusLabel, Supplier
from core.managers import set_current_membership, set_current_tenant
from core.tests.mixins import TenantTestMixin
from inventory.models import Component, ComponentStock
from itambox.capabilities import registry
from organization.models import Location, Site, Tenant
from procurement.models import FulfillmentLink, PurchaseOrder, PurchaseOrderLine
from procurement.services import approve_purchase_order, order_purchase_order, receive_purchase_order

User = get_user_model()


class SeamUpgradePreservationTests(TenantTestMixin, TransactionTestCase):
    """The Stable promotion preserves Beta-era fulfilment data unchanged."""

    def setUp(self):
        super().setUp()
        StatusLabel.objects.create(name="Deployable", slug="seam-upgrade-deployable", type="deployable")
        self.tenant = Tenant.objects.create(name="Seam upgrade tenant", slug="seam-upgrade-tenant")
        self.site = Site.objects.create(name="Seam upgrade site", slug="seam-upgrade-site")
        self.location = Location.objects.create(
            name="Seam upgrade location", slug="seam-upgrade-location", site=self.site, tenant=self.tenant
        )
        self.supplier = Supplier.objects.create(name="Seam upgrade supplier", slug="seam-upgrade-supplier")
        self.manufacturer = Manufacturer.objects.create(name="Seam upgrade maker", slug="seam-upgrade-maker")
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer, model="Seam upgrade model", slug="seam-upgrade-model"
        )
        self.actor = User.objects.create_superuser(
            username="seam-upgrade-admin", email="seam-upgrade@example.com", password="password"
        )
        self.component = Component.objects.create(name="Seam upgrade RAM", manufacturer=self.manufacturer)

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)
        super().tearDown()

    @staticmethod
    def _snapshot(row):
        return {field.attname: getattr(row, field.attname) for field in row._meta.concrete_fields}

    def _ordered_component_purchase_order(self, number, qty_ordered):
        purchase_order = PurchaseOrder.objects.create(
            tenant=self.tenant,
            order_number=number,
            currency="EUR",
            supplier=self.supplier,
            destination_location=self.location,
            created_by=self.actor,
        )
        line = PurchaseOrderLine.objects.create(
            tenant=self.tenant,
            purchase_order=purchase_order,
            component=self.component,
            qty_ordered=qty_ordered,
            unit_price="5.00",
        )
        approve_purchase_order(purchase_order)
        order_purchase_order(purchase_order)
        return purchase_order, line

    def _beta_request(self, **kwargs):
        request = AssetRequest(
            tenant=self.tenant, requester=self.actor, status=RequestStatusChoices.PROCUREMENT, **kwargs
        )
        request._skip_duplicate_check = True
        request.save()
        return request

    def test_the_promotion_does_not_rewrite_recorded_beta_links(self):
        purchase_order, line = self._ordered_component_purchase_order("PO-UPGRADE-001", 10)
        request = self._beta_request(component=self.component, qty=10)
        link = FulfillmentLink.objects.create(
            tenant=self.tenant, asset_request=request, purchase_order_line=line, qty_allocated=10
        )
        ComponentStock.objects.create(component=self.component, location=self.location, qty=3)
        before = (self._snapshot(link), self._snapshot(request), self._snapshot(line))

        self.assertTrue(registry.is_active("procurement.requisition_seam"))

        for row, snapshot in zip((link, request, line), before, strict=True):
            row.refresh_from_db()
            self.assertEqual(self._snapshot(row), snapshot)
        self.assertIsNone(link.qty_received)
        self.assertEqual(link.qty_outstanding, 10)
        self.assertEqual(request.status, RequestStatusChoices.PROCUREMENT)
        self.assertEqual(ComponentStock.objects.get(component=self.component, location=self.location).qty, 3)

    def test_untracked_links_complete_on_the_next_full_receipt(self):
        purchase_order, line = self._ordered_component_purchase_order("PO-UPGRADE-002", 10)
        request = self._beta_request(component=self.component, qty=10)
        link = FulfillmentLink.objects.create(
            tenant=self.tenant, asset_request=request, purchase_order_line=line, qty_allocated=10
        )
        self.assertIsNone(link.qty_received)

        receive_purchase_order(purchase_order, {line.pk: 10})

        request.refresh_from_db()
        link.refresh_from_db()
        self.assertEqual(request.status, RequestStatusChoices.APPROVED)
        self.assertEqual((link.qty_received, link.qty_allocated), (10, 10))
        self.assertTrue(link.fully_delivered)
        self.assertEqual(ComponentStock.objects.get(component=self.component, location=self.location).qty, 10)

    def test_serialised_beta_children_survive_and_the_remaining_unit_completes(self):
        purchase_order = PurchaseOrder.objects.create(
            tenant=self.tenant,
            order_number="PO-UPGRADE-003",
            currency="EUR",
            supplier=self.supplier,
            destination_location=self.location,
            created_by=self.actor,
        )
        line = PurchaseOrderLine.objects.create(
            tenant=self.tenant,
            purchase_order=purchase_order,
            asset_type=self.asset_type,
            qty_ordered=2,
            unit_price="10.00",
        )
        approve_purchase_order(purchase_order)
        order_purchase_order(purchase_order)

        parent = AssetRequest(
            tenant=self.tenant,
            requester=self.actor,
            asset_type=self.asset_type,
            qty=2,
            is_group=True,
            status=RequestStatusChoices.PROCUREMENT,
        )
        parent._skip_duplicate_check = True
        parent.save()
        children = []
        for status in (RequestStatusChoices.APPROVED, RequestStatusChoices.PROCUREMENT):
            child = AssetRequest(
                tenant=self.tenant,
                requester=self.actor,
                asset_type=self.asset_type,
                qty=1,
                parent=parent,
                status=status,
            )
            child._skip_duplicate_check = True
            child.save()
            children.append(child)

        delivered = Asset.objects.create(
            name="Delivered before the upgrade",
            asset_type=self.asset_type,
            serial_number="UPG-SN-1",
            status=StatusLabel.objects.get(type="deployable"),
            location=self.location,
            supplier=self.supplier,
            purchase_cost=line.unit_price,
            currency="EUR",
            purchase_date=timezone.now().date(),
            order_number=purchase_order.order_number,
            tenant=self.tenant,
            purchase_order_line=line,
        )
        children[0].asset = delivered
        children[0].save(update_fields=["asset"])
        delivered_child_before = self._snapshot(children[0])

        FulfillmentLink.objects.create(
            tenant=self.tenant, asset_request=children[0], purchase_order_line=line, qty_allocated=1
        )
        FulfillmentLink.objects.create(
            tenant=self.tenant, asset_request=children[1], purchase_order_line=line, qty_allocated=1
        )
        line.qty_received = 1
        line.save(update_fields=["qty_received"])

        receive_purchase_order(
            purchase_order,
            {line.pk: 1},
            [{"line_id": line.pk, "serial_number": "UPG-SN-2", "asset_tag": "UPG-TAG-2"}],
        )

        children[0].refresh_from_db()
        children[1].refresh_from_db()
        parent.refresh_from_db()
        self.assertEqual(self._snapshot(children[0]), delivered_child_before)
        self.assertEqual(children[1].status, RequestStatusChoices.APPROVED)
        self.assertIsNotNone(children[1].asset_id)
        self.assertEqual(parent.status, RequestStatusChoices.APPROVED)
        self.assertEqual(Asset._base_manager.filter(purchase_order_line=line).count(), 2)
        self.assertEqual(FulfillmentLink.objects.get(asset_request=children[1]).qty_received, 1)
        self.assertIsNone(FulfillmentLink.objects.get(asset_request=children[0]).qty_received)
