"""Procurement journeys: purchase-order transitions are serialised and re-linking is idempotent."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connections, transaction
from django.test import TestCase, TransactionTestCase

from assets.choices import RequestStatusChoices
from assets.models import Asset, AssetRequest, AssetType, Manufacturer, StatusLabel, Supplier
from core.context import set_current_tenant
from organization.models import Location, Site, Tenant
from procurement.models import PurchaseOrder, PurchaseOrderLine
from procurement.services import (
    cancel_purchase_order,
    link_asset_request_to_purchase_order,
    receive_purchase_order,
    reopen_purchase_order,
)

from .support import JourneyMixin

User = get_user_model()


class PurchaseOrderRelinkJourneyTests(JourneyMixin, TestCase):
    def setUp(self):
        self.make_tenant("journey-po-relink")
        self.operator = self.make_member(
            "po-operator",
            {"procurement.change_purchaseorder", "procurement.view_purchaseorder", "assets.fulfill_assetrequest"},
        )
        manufacturer = Manufacturer.objects.create(name="Journey Maker", slug="journey-maker")
        self.asset_type = AssetType.objects.create(
            manufacturer=manufacturer, model="Journey PO Model", slug="journey-po-model", requestable=True
        )
        site = Site.objects.create(name="Journey Relink Site", slug="journey-relink-site")
        location = Location.objects.create(
            name="Journey Relink Loc", slug="journey-relink-loc", site=site, tenant=self.tenant
        )
        self.po = PurchaseOrder.objects.create(
            tenant=self.tenant,
            destination_location=location,
            order_number="PO-JOURNEY-RELINK",
            supplier=Supplier.objects.create(name="Journey Supplier", slug="journey-supplier"),
            created_by=self.operator,
        )
        self.asset_request = AssetRequest.objects.create(
            tenant=self.tenant,
            requester=self.operator,
            asset_type=self.asset_type,
            qty=1,
            status=RequestStatusChoices.APPROVED,
        )

    @pytest.mark.xfail(strict=True, reason="reopen leaves the old line, relink adds another (#606)")
    def test_cancel_reopen_relink_yields_one_line_per_request_unit(self):
        self.client_login_to_tenant(self.operator, self.tenant)
        link_asset_request_to_purchase_order(self.po, self.asset_request.pk, user=self.operator)
        cancel_purchase_order(self.po)
        reopen_purchase_order(self.po)
        self.po.refresh_from_db()

        link_asset_request_to_purchase_order(self.po, self.asset_request.pk, user=self.operator)

        self.assertEqual(PurchaseOrderLine.objects.filter(purchase_order=self.po).count(), 1)


@pytest.mark.serial_only
class PurchaseOrderCancelReceiveRaceJourneyTests(TransactionTestCase):
    """A cancel racing a receipt must never leave a cancelled PO that holds received stock."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Journey PO Race", slug="journey-po-race")
        site = Site.objects.create(name="Journey Race Site", slug="journey-race-site")
        location = Location.objects.create(
            name="Journey Race Loc", slug="journey-race-loc", site=site, tenant=self.tenant
        )
        manufacturer = Manufacturer.objects.create(name="Journey Race Maker", slug="journey-race-maker")
        asset_type = AssetType.objects.create(manufacturer=manufacturer, model="Race Model", slug="journey-race-model")
        StatusLabel.objects.create(name="Deployable", slug="journey-race-deployable", type="deployable")
        creator = User.objects.create_user(username="journey-race-creator", password="x")
        self.po = PurchaseOrder.objects.create(
            tenant=self.tenant,
            order_number="PO-JOURNEY-RACE",
            currency="USD",
            supplier=Supplier.objects.create(name="Journey Race Supplier", slug="journey-race-supplier"),
            destination_location=location,
            created_by=creator,
            status=PurchaseOrder.STATUS_ORDERED,
        )
        self.line = PurchaseOrderLine.objects.create(
            tenant=self.tenant, purchase_order=self.po, asset_type=asset_type, qty_ordered=1, unit_price="10.00"
        )

    @pytest.mark.xfail(strict=True, reason="cancel and receive do not lock the PO row (#606)")
    def test_concurrent_cancel_and_receive_never_cancel_a_po_with_received_stock(self):
        receipt_written = Event()
        release_receipt = Event()
        stale_po = PurchaseOrder.objects.get(pk=self.po.pk)

        def receive():
            close_old_connections()
            set_current_tenant(self.tenant)
            try:
                po = PurchaseOrder.objects.get(pk=self.po.pk)
                with transaction.atomic():
                    receive_purchase_order(po, {self.line.pk: 1}, expected_received={self.line.pk: 0})
                    # The receipt is written but not committed: hold the transaction open.
                    receipt_written.set()
                    release_receipt.wait(timeout=10)
            finally:
                set_current_tenant(None)
                connections.close_all()

        def cancel():
            close_old_connections()
            set_current_tenant(self.tenant)
            try:
                cancel_purchase_order(stale_po)
            except ValidationError:
                pass
            finally:
                set_current_tenant(None)
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            receiving = executor.submit(receive)
            self.assertTrue(receipt_written.wait(timeout=10))
            cancelling = executor.submit(cancel)
            # Give the cancel time to run (unlocked) or to queue behind the PO row lock.
            Event().wait(1.0)
            release_receipt.set()
            receiving.result(timeout=20)
            cancelling.result(timeout=20)

        self.po.refresh_from_db()
        received_assets = Asset._base_manager.filter(tenant=self.tenant).count()
        self.assertFalse(
            self.po.status == PurchaseOrder.STATUS_CANCELLED and received_assets > 0,
            f"PO is {self.po.status} yet {received_assets} received asset(s) exist",
        )
