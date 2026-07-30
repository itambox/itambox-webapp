from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from assets.models import Asset, AssetType, Manufacturer, StatusLabel, Supplier
from organization.models import Location, Site, Tenant
from procurement.models import PurchaseOrder, PurchaseOrderLine
from procurement.services import receive_purchase_order

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

        receive_purchase_order(purchase_order, {line.pk: 1})

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
