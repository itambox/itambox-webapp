from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from assets.models import Asset, AssetType, Manufacturer, StatusLabel, Supplier
from organization.models import Location, Site, Tenant
from procurement.models import PurchaseOrder, PurchaseOrderLine
from procurement.services import (
    approve_purchase_order,
    cancel_purchase_order,
    order_purchase_order,
    receive_purchase_order,
    reopen_purchase_order,
)

User = get_user_model()


class ProcurementCurrencyInvariantTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="PO Currency Tenant", slug="po-currency-tenant")
        site = Site.objects.create(name="PO Currency Site", slug="po-currency-site")
        self.location = Location.objects.create(
            name="PO Currency Location",
            slug="po-currency-location",
            site=site,
            tenant=self.tenant,
        )
        self.supplier = Supplier.objects.create(name="PO Currency Supplier", slug="po-currency-supplier")
        manufacturer = Manufacturer.objects.create(
            name="PO Currency Manufacturer",
            slug="po-currency-manufacturer",
        )
        self.asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="PO Currency Model",
            slug="po-currency-model",
        )
        StatusLabel.objects.create(name="Deployable", slug="po-currency-deployable", type="deployable")
        self.user = User.objects.create_user(username="po-currency-user", password="password")

    def test_received_asset_preserves_purchase_order_currency(self):
        purchase_order = PurchaseOrder.objects.create(
            tenant=self.tenant,
            order_number="PO-USD-001",
            currency="USD",
            status=PurchaseOrder.STATUS_ORDERED,
            supplier=self.supplier,
            destination_location=self.location,
            created_by=self.user,
        )
        line = PurchaseOrderLine.objects.create(
            tenant=self.tenant,
            purchase_order=purchase_order,
            asset_type=self.asset_type,
            qty_ordered=1,
            unit_price=Decimal("123.45"),
        )

        receive_purchase_order(purchase_order, {line.pk: 1}, expected_received={line.pk: line.qty_received})

        asset = Asset._base_manager.get(purchase_order_line=line)
        self.assertEqual(asset.purchase_cost, Decimal("123.45"))
        self.assertEqual(asset.currency, "USD")

    def test_purchase_order_line_currency_is_delegated_from_single_parent(self):
        purchase_order = PurchaseOrder.objects.create(
            tenant=self.tenant,
            order_number="PO-EUR-001",
            currency="EUR",
            supplier=self.supplier,
            destination_location=self.location,
            created_by=self.user,
        )
        first_line = PurchaseOrderLine.objects.create(
            tenant=self.tenant,
            purchase_order=purchase_order,
            asset_type=self.asset_type,
            qty_ordered=2,
            unit_price=Decimal("10.00"),
        )
        second_line = PurchaseOrderLine.objects.create(
            tenant=self.tenant,
            purchase_order=purchase_order,
            asset_type=self.asset_type,
            qty_ordered=1,
            unit_price=Decimal("5.00"),
        )

        self.assertEqual(first_line.currency, "EUR")
        self.assertEqual(second_line.currency, "EUR")
        self.assertEqual(first_line.total_cost + second_line.total_cost, Decimal("25.00"))


class PurchaseOrderTransitionInvariantTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="PO Transition Tenant", slug="po-transition-tenant")
        site = Site.objects.create(name="PO Transition Site", slug="po-transition-site")
        self.location = Location.objects.create(
            name="PO Transition Location",
            slug="po-transition-location",
            site=site,
            tenant=self.tenant,
        )
        self.supplier = Supplier.objects.create(name="PO Transition Supplier", slug="po-transition-supplier")
        self.user = User.objects.create_user(username="po-transition-user", password="password")

    def test_every_illegal_status_transition_is_rejected(self):
        purchase_order = PurchaseOrder.objects.create(
            tenant=self.tenant,
            order_number="PO-TRANSITION-001",
            currency="EUR",
            supplier=self.supplier,
            destination_location=self.location,
            created_by=self.user,
        )
        all_statuses = {status for status, _label in PurchaseOrder.STATUS_CHOICES}
        transitions = {
            "approve": ({PurchaseOrder.STATUS_DRAFT}, approve_purchase_order),
            "order": ({PurchaseOrder.STATUS_APPROVED}, order_purchase_order),
            "receive": ({PurchaseOrder.STATUS_ORDERED, PurchaseOrder.STATUS_PARTIAL}, receive_purchase_order),
            "cancel": (
                {PurchaseOrder.STATUS_DRAFT, PurchaseOrder.STATUS_APPROVED, PurchaseOrder.STATUS_ORDERED},
                cancel_purchase_order,
            ),
            "reopen": ({PurchaseOrder.STATUS_CANCELLED}, reopen_purchase_order),
        }

        for action, (allowed_statuses, operation) in transitions.items():
            for status in sorted(all_statuses - allowed_statuses):
                with self.subTest(action=action, status=status):
                    stale_purchase_order = PurchaseOrder.objects.get(pk=purchase_order.pk)
                    stale_purchase_order.status = next(iter(allowed_statuses))
                    PurchaseOrder.objects.filter(pk=purchase_order.pk).update(status=status)
                    expected_display = PurchaseOrder.objects.get(pk=purchase_order.pk).get_status_display()
                    with self.assertRaises(ValidationError) as exc:
                        if action == "receive":
                            operation(stale_purchase_order, {}, expected_received={})
                        else:
                            operation(stale_purchase_order)
                    self.assertIn(expected_display, exc.exception.messages[0])
                    purchase_order.refresh_from_db()
                    self.assertEqual(purchase_order.status, status)
