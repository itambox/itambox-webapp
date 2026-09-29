from concurrent.futures import ThreadPoolExecutor
from secrets import token_hex
from threading import Barrier, Event, Lock
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connections
from django.test import TransactionTestCase

from assets.choices import RequestStatusChoices
from assets.models import Asset, AssetRequest, AssetType, Manufacturer, StatusLabel, Supplier
from core.context import set_current_tenant
from organization.models import Location, Site, Tenant
from procurement.models import FulfillmentLink, PurchaseOrder, PurchaseOrderLine
from procurement.services import approve_purchase_order, order_purchase_order, receive_purchase_order

User = get_user_model()


@pytest.mark.serial_only
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
        self.assertIn("only 0 remain outstanding", loser_message)
        self.line.refresh_from_db()
        self.purchase_order.refresh_from_db()
        self.assertEqual(self.line.qty_received, 1)
        self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_RECEIVED)
        self.assertEqual(Asset._base_manager.filter(purchase_order_line=self.line).count(), 1)


@pytest.mark.serial_only
class FulfillmentAttributionConcurrencyTests(TransactionTestCase):
    """Concurrent receipts attribute delivered quantities exactly once (#569)."""

    def setUp(self):
        StatusLabel.objects.create(name="Deployable", slug="po-attribution-deployable", type="deployable")
        self.tenant = Tenant.objects.create(name="Attribution Tenant", slug="attribution-tenant")
        self.site = Site.objects.create(name="Attribution Site", slug="attribution-site")
        self.location = Location.objects.create(
            name="Attribution Location",
            slug="attribution-location",
            site=self.site,
            tenant=self.tenant,
        )
        self.supplier = Supplier.objects.create(name="Attribution Supplier", slug="attribution-supplier")
        self.manufacturer = Manufacturer.objects.create(
            name="Attribution Manufacturer", slug="attribution-manufacturer"
        )
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="Attribution Model",
            slug="attribution-model",
        )
        self.requester = User.objects.create_user(username="attribution-requester", password="password")

    def _run_two(self, operation):
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
            return list(executor.map(lambda _index: invoke(), range(2)))

    def _draft_purchase_order(self, order_number):
        self.purchase_order = PurchaseOrder.objects.create(
            tenant=self.tenant,
            order_number=order_number,
            currency="EUR",
            supplier=self.supplier,
            destination_location=self.location,
            created_by=self.requester,
        )
        return self.purchase_order

    def test_concurrent_partial_receipts_attribute_each_unit_exactly_once(self):
        from assets.models import Category
        from inventory.models import Component, ComponentStock

        category = Category.objects.create(
            name="Race Components", slug="race-components", applies_to={"component": True}
        )
        component = Component.objects.create(name="Race RAM", manufacturer=self.manufacturer, category=category)
        purchase_order = self._draft_purchase_order("PO-RACE-ATTR-001")
        line = PurchaseOrderLine.objects.create(
            tenant=self.tenant,
            purchase_order=purchase_order,
            component=component,
            qty_ordered=10,
            unit_price="5.00",
        )
        approve_purchase_order(purchase_order)
        order_purchase_order(purchase_order)
        request = AssetRequest.objects.create(
            tenant=self.tenant,
            requester=self.requester,
            component=component,
            qty=10,
            status=RequestStatusChoices.PROCUREMENT,
        )
        link = FulfillmentLink.objects.create(
            tenant=self.tenant,
            asset_request=request,
            purchase_order_line=line,
            qty_allocated=10,
            qty_received=0,
        )

        results = self._run_two(lambda purchase_order: receive_purchase_order(purchase_order, {line.pk: 3}))

        self.assertEqual([kind for kind, _message in results].count("success"), 2, results)
        line.refresh_from_db()
        link.refresh_from_db()
        request.refresh_from_db()
        self.assertEqual(line.qty_received, 6)
        self.assertEqual(link.qty_received, 6)
        self.assertEqual(link.qty_outstanding, 4)
        self.assertEqual(request.status, RequestStatusChoices.PROCUREMENT)
        self.assertEqual(
            ComponentStock.objects.get(component=component, location=self.location).qty,
            6,
        )

    def test_concurrent_serialised_receipts_materialise_each_unit_for_a_distinct_request(self):
        purchase_order = self._draft_purchase_order("PO-RACE-ATTR-002")
        line = PurchaseOrderLine.objects.create(
            tenant=self.tenant,
            purchase_order=purchase_order,
            asset_type=self.asset_type,
            qty_ordered=2,
            unit_price="10.00",
        )
        approve_purchase_order(purchase_order)
        order_purchase_order(purchase_order)
        parent = AssetRequest.objects.create(
            tenant=self.tenant,
            requester=self.requester,
            asset_type=self.asset_type,
            qty=2,
            is_group=True,
            status=RequestStatusChoices.PROCUREMENT,
        )
        children = []
        for _ in range(2):
            child = AssetRequest(
                tenant=self.tenant,
                requester=self.requester,
                asset_type=self.asset_type,
                qty=1,
                parent=parent,
                status=RequestStatusChoices.PROCUREMENT,
            )
            child._skip_duplicate_check = True
            child.save()
            children.append(child)
        for child in children:
            FulfillmentLink.objects.create(
                tenant=self.tenant,
                asset_request=child,
                purchase_order_line=line,
                qty_allocated=1,
                qty_received=0,
            )

        def receive_one(purchase_order):
            token = token_hex(8)
            receive_purchase_order(
                purchase_order,
                {line.pk: 1},
                [{"line_id": line.pk, "serial_number": f"RACE-SN-{token}", "asset_tag": f"RACE-TAG-{token}"}],
            )

        results = self._run_two(receive_one)

        self.assertEqual([kind for kind, _message in results].count("success"), 2, results)
        delivered_children = [AssetRequest.objects.get(pk=child.pk) for child in children]
        for child in delivered_children:
            self.assertEqual(child.status, RequestStatusChoices.APPROVED)
            self.assertIsNotNone(child.asset_id)
        self.assertEqual(len({child.asset_id for child in delivered_children}), 2)
        parent.refresh_from_db()
        self.assertEqual(parent.status, RequestStatusChoices.APPROVED)
        self.assertEqual(Asset._base_manager.filter(purchase_order_line=line).count(), 2)
        for child in delivered_children:
            link = FulfillmentLink.objects.get(asset_request=child)
            self.assertEqual((link.qty_received, link.qty_allocated), (1, 1))
            self.assertTrue(link.fully_delivered)
