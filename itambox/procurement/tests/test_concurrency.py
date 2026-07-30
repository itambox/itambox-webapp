from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connections
from django.test import TransactionTestCase

from assets.models import Asset, AssetType, Manufacturer, StatusLabel, Supplier
from core.context import set_current_tenant
from organization.models import Location, Site, Tenant
from procurement.models import PurchaseOrder, PurchaseOrderLine
from procurement.services import approve_purchase_order, receive_purchase_order

User = get_user_model()


class PurchaseOrderConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="PO Race Tenant", slug="po-race-tenant")
        self.site = Site.objects.create(name="PO Race Site", slug="po-race-site")
        self.location = Location.objects.create(
            name="PO Race Location",
            slug="po-race-location",
            site=self.site,
            tenant=self.tenant,
        )
        self.supplier = Supplier.objects.create(name="PO Race Supplier", slug="po-race-supplier")
        self.manufacturer = Manufacturer.objects.create(name="PO Race Manufacturer", slug="po-race-manufacturer")
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="PO Race Model",
            slug="po-race-model",
        )
        StatusLabel.objects.create(name="Deployable", slug="po-race-deployable", type="deployable")
        self.creator = User.objects.create_user(username="po-race-creator", password="password")
        self.approver = User.objects.create_user(username="po-race-approver", password="password")
        self.purchase_order = PurchaseOrder.objects.create(
            tenant=self.tenant,
            order_number="PO-RACE-001",
            currency="USD",
            supplier=self.supplier,
            destination_location=self.location,
            created_by=self.creator,
        )
        self.line = PurchaseOrderLine.objects.create(
            tenant=self.tenant,
            purchase_order=self.purchase_order,
            asset_type=self.asset_type,
            qty_ordered=1,
            unit_price="10.00",
        )

    def _run_race(self, operation):
        barrier = Barrier(2)

        def invoke():
            close_old_connections()
            set_current_tenant(self.tenant)
            try:
                purchase_order = PurchaseOrder.objects.get(pk=self.purchase_order.pk)
                barrier.wait(timeout=10)
                operation(purchase_order)
                return ("success", "")
            except ValidationError as exc:
                return ("validation_error", str(exc))
            finally:
                set_current_tenant(None)
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: invoke(), range(2)))
        return results

    def test_concurrent_approvals_produce_one_transition_and_visible_loser(self):
        results = self._run_race(lambda purchase_order: approve_purchase_order(purchase_order, user=self.approver))

        self.assertEqual([kind for kind, _message in results].count("success"), 1, results)
        self.assertEqual([kind for kind, _message in results].count("validation_error"), 1, results)
        loser_message = next(message for kind, message in results if kind == "validation_error")
        self.assertIn("Approved", loser_message)
        self.purchase_order.refresh_from_db()
        self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_APPROVED)

    def test_concurrent_receipts_never_over_receive_or_double_materialize(self):
        self.purchase_order.status = PurchaseOrder.STATUS_ORDERED
        self.purchase_order.save(update_fields=["status"])

        first_materialization_started = Event()
        second_materialization_started = Event()
        release_first_materialization = Event()
        call_lock = Lock()
        create_calls = 0
        asset_manager_class = type(Asset.objects)
        original_create = asset_manager_class.create

        def controlled_create(manager, *args, **kwargs):
            nonlocal create_calls
            with call_lock:
                create_calls += 1
                call_number = create_calls
            if call_number == 1:
                first_materialization_started.set()
                if not release_first_materialization.wait(timeout=10):
                    raise AssertionError("Timed out while holding the first receipt transaction open")
            else:
                second_materialization_started.set()
            return original_create(manager, *args, **kwargs)

        def invoke():
            close_old_connections()
            set_current_tenant(self.tenant)
            try:
                purchase_order = PurchaseOrder.objects.get(pk=self.purchase_order.pk)
                receive_purchase_order(purchase_order, {self.line.pk: 1})
                return ("success", "")
            except ValidationError as exc:
                return ("validation_error", str(exc))
            finally:
                set_current_tenant(None)
                connections.close_all()

        with patch.object(asset_manager_class, "create", controlled_create):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(invoke)
                self.assertTrue(first_materialization_started.wait(timeout=10))
                second = executor.submit(invoke)
                try:
                    self.assertFalse(
                        second_materialization_started.wait(timeout=1),
                        "The second receipt materialized before the first transaction released its line lock",
                    )
                finally:
                    release_first_materialization.set()
                results = [first.result(timeout=10), second.result(timeout=10)]
        self.assertEqual([kind for kind, _message in results].count("success"), 1, results)
        self.assertEqual([kind for kind, _message in results].count("validation_error"), 1, results)
        loser_message = next(message for kind, message in results if kind == "validation_error")
        self.assertIn("only 0 outstanding", loser_message)
        self.line.refresh_from_db()
        self.purchase_order.refresh_from_db()
        self.assertEqual(self.line.qty_received, 1)
        self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_RECEIVED)
        self.assertEqual(Asset._base_manager.filter(purchase_order_line=self.line).count(), 1)
