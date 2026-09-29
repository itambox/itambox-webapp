"""Partial-receipt and release regressions for the Asset Request Procurement Seam.

These regressions pin the quantity and allocation semantics of the seam that was
promoted from Beta to Stable before the RC.1 source freeze (#569). Against the
pre-promotion implementation the partial-receipt, allocation-order, and release
tests below fail: a positive receipt used to approve every linked request at
once, and cancelled requests kept their purchase-order reservation.

Semantics under test:

* serialised receipts allocate exactly one asset per single-unit request unit
  and approve the unit only once its full pledged quantity was received;
* quantity receipts attribute delivered units across a line's outstanding
  requests in deterministic (request_date, pk) order, approve each request only
  on full delivery, and keep surplus units as free stock;
* cancelled request units release their fulfilment links while the delivered
  quantity record stays readable on the closed link;
* legacy links without receipt attribution stay reserved until a fresh receipt
  completes them — no historical quantities are invented.
"""

import datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from assets.choices import RequestStatusChoices
from assets.models import Asset, AssetRequest, AssetType, Category, Manufacturer, StatusLabel, Supplier
from inventory.models import Component, ComponentStock
from organization.models import Location, Site, Tenant
from procurement.models import FulfillmentLink, PurchaseOrder, PurchaseOrderLine
from procurement.services import (
    approve_purchase_order,
    link_asset_request_to_purchase_order,
    order_purchase_order,
    receive_purchase_order,
)

User = get_user_model()


def _expected(line):
    """Recorded receipt quantities a receipt submission is prepared against."""
    line.refresh_from_db()
    return {line.pk: line.qty_received}


class PartialReceiptFixture(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="partial-receipts", email="partial@example.com", password="password"
        )

        # receive_purchase_order() unconditionally requires a 'deployable'
        # StatusLabel to exist.
        StatusLabel.objects.get_or_create(name="Deployable", defaults={"type": "deployable", "slug": "deployable"})

        self.tenant = Tenant.objects.create(name="Partial Receipt Tenant", slug="partial-receipt-tenant")
        self.site = Site.objects.create(name="Partial Site", slug="partial-site")
        self.location = Location.objects.create(
            name="Partial Location", slug="partial-location", site=self.site, tenant=self.tenant
        )
        self.supplier = Supplier.objects.create(name="Partial Supplier", slug="partial-supplier")
        self.manufacturer = Manufacturer.objects.create(name="Partial Manufacturer", slug="partial-manufacturer")
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer, model="Partial Model", slug="partial-model", requestable=True
        )

    def _draft_purchase_order(self, order_number):
        return PurchaseOrder.objects.create(
            tenant=self.tenant,
            order_number=order_number,
            currency="EUR",
            supplier=self.supplier,
            destination_location=self.location,
            created_by=self.user,
        )

    def _open(self, purchase_order):
        approve_purchase_order(purchase_order)
        order_purchase_order(purchase_order)

    def _component(self, name, slug):
        category, _created = Category.objects.get_or_create(
            name="Partial Components",
            slug="partial-components",
            defaults={"applies_to": {"component": True}},
        )
        return Component.objects.create(name=name, manufacturer=self.manufacturer, category=category)

    def _request(self, **kwargs):
        kwargs.setdefault("status", RequestStatusChoices.APPROVED)
        request = AssetRequest(tenant=self.tenant, requester=self.user, **kwargs)
        request._skip_duplicate_check = True
        request.save()
        return request

    def _group_with_children(self, count):
        parent = AssetRequest(
            tenant=self.tenant,
            requester=self.user,
            asset_type=self.asset_type,
            qty=count,
            is_group=True,
            status=RequestStatusChoices.APPROVED,
        )
        parent._skip_duplicate_check = True
        parent.save()
        children = []
        for _ in range(count):
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
        return parent, children

    @staticmethod
    def _serial_details(line, indexes):
        return [
            {"line_id": line.pk, "serial_number": f"P-SN-{index}", "asset_tag": f"P-TAG-{index}"} for index in indexes
        ]


class PartialReceiptSemanticsTests(PartialReceiptFixture):
    def test_group_receipt_approves_exactly_the_delivered_units(self):
        purchase_order = self._draft_purchase_order("PO-PARTIAL-GROUP-001")
        parent, children = self._group_with_children(10)
        link = link_asset_request_to_purchase_order(purchase_order, parent.pk, user=self.user)
        line = link.purchase_order_line
        self._open(purchase_order)

        receive_purchase_order(
            purchase_order,
            {line.pk: 2},
            self._serial_details(line, range(2)),
            expected_received=_expected(line),
        )

        for child in children:
            child.refresh_from_db()
        approved = {child.pk for child in children if child.status == RequestStatusChoices.APPROVED}
        self.assertEqual(approved, {child.pk for child in children[:2]})
        self.assertEqual([child.status for child in children].count(RequestStatusChoices.PROCUREMENT), 8)
        parent.refresh_from_db()
        self.assertEqual(parent.status, RequestStatusChoices.PROCUREMENT)
        for child in children[:2]:
            delivered_link = FulfillmentLink.objects.get(asset_request=child)
            self.assertEqual((delivered_link.qty_received, delivered_link.qty_allocated), (1, 1))
            self.assertTrue(delivered_link.fully_delivered)
        self.assertEqual(FulfillmentLink.objects.get(asset_request=children[2]).qty_received, 0)

        receive_purchase_order(
            purchase_order,
            {line.pk: 3},
            self._serial_details(line, range(2, 5)),
            expected_received=_expected(line),
        )

        for child in children:
            child.refresh_from_db()
        approved = {child.pk for child in children if child.status == RequestStatusChoices.APPROVED}
        self.assertEqual(approved, {child.pk for child in children[:5]})
        self.assertEqual([child.status for child in children].count(RequestStatusChoices.PROCUREMENT), 5)
        parent.refresh_from_db()
        self.assertEqual(parent.status, RequestStatusChoices.PROCUREMENT)

        receive_purchase_order(
            purchase_order,
            {line.pk: 5},
            self._serial_details(line, range(5, 10)),
            expected_received=_expected(line),
        )

        parent.refresh_from_db()
        self.assertEqual(parent.status, RequestStatusChoices.APPROVED)
        for child in children:
            child.refresh_from_db()
            self.assertEqual(child.status, RequestStatusChoices.APPROVED)
            self.assertIsNotNone(child.asset_id)
            self.assertTrue(FulfillmentLink.objects.get(asset_request=child).fully_delivered)
        self.assertEqual(Asset._base_manager.filter(purchase_order_line=line).count(), 10)

    def test_component_walkthrough_partial_receipts_never_approve_early(self):
        component = self._component("Walkthrough RAM", "walkthrough-ram")
        purchase_order = self._draft_purchase_order("PO-PARTIAL-COMP-001")
        request = self._request(component=component, qty=10)
        link = link_asset_request_to_purchase_order(purchase_order, request.pk, user=self.user)
        line = link.purchase_order_line
        self._open(purchase_order)

        receive_purchase_order(purchase_order, {line.pk: 2}, expected_received=_expected(line))

        line.refresh_from_db()
        link.refresh_from_db()
        request.refresh_from_db()
        purchase_order.refresh_from_db()
        self.assertEqual(line.qty_received, 2)
        self.assertEqual(link.qty_received, 2)
        self.assertEqual(link.qty_outstanding, 8)
        self.assertFalse(link.fully_delivered)
        self.assertEqual(request.status, RequestStatusChoices.PROCUREMENT)
        self.assertEqual(ComponentStock.objects.get(component=component, location=self.location).qty, 2)
        self.assertEqual(purchase_order.status, PurchaseOrder.STATUS_PARTIAL)

        receive_purchase_order(purchase_order, {line.pk: 3}, expected_received=_expected(line))

        link.refresh_from_db()
        request.refresh_from_db()
        self.assertEqual(link.qty_received, 5)
        self.assertEqual(link.qty_outstanding, 5)
        self.assertEqual(request.status, RequestStatusChoices.PROCUREMENT)
        self.assertEqual(ComponentStock.objects.get(component=component, location=self.location).qty, 5)

        receive_purchase_order(purchase_order, {line.pk: 5}, expected_received=_expected(line))

        line.refresh_from_db()
        link.refresh_from_db()
        request.refresh_from_db()
        purchase_order.refresh_from_db()
        self.assertEqual(line.qty_received, 10)
        self.assertEqual(link.qty_received, 10)
        self.assertEqual(link.qty_outstanding, 0)
        self.assertTrue(link.fully_delivered)
        self.assertEqual(request.status, RequestStatusChoices.APPROVED)
        self.assertEqual(ComponentStock.objects.get(component=component, location=self.location).qty, 10)
        self.assertEqual(purchase_order.status, PurchaseOrder.STATUS_RECEIVED)

    def test_quantity_attribution_follows_request_date_order(self):
        component = self._component("Ordered RAM", "ordered-ram")
        purchase_order = self._draft_purchase_order("PO-PARTIAL-ORDER-001")
        line = PurchaseOrderLine.objects.create(
            tenant=self.tenant,
            purchase_order=purchase_order,
            component=component,
            qty_ordered=4,
            unit_price="5.00",
        )
        self._open(purchase_order)
        late_request = self._request(component=component, qty=2, status=RequestStatusChoices.PROCUREMENT)
        early_request = self._request(component=component, qty=2, status=RequestStatusChoices.PROCUREMENT)
        # The later-dated request is linked first so its link has the smaller pk.
        FulfillmentLink.objects.create(
            tenant=self.tenant,
            asset_request=late_request,
            purchase_order_line=line,
            qty_allocated=2,
            qty_received=0,
        )
        FulfillmentLink.objects.create(
            tenant=self.tenant,
            asset_request=early_request,
            purchase_order_line=line,
            qty_allocated=2,
            qty_received=0,
        )
        AssetRequest.objects.filter(pk=early_request.pk).update(
            request_date=timezone.now() - datetime.timedelta(minutes=5)
        )
        AssetRequest.objects.filter(pk=late_request.pk).update(
            request_date=timezone.now() + datetime.timedelta(minutes=5)
        )
        early_request.refresh_from_db()
        late_request.refresh_from_db()

        receive_purchase_order(purchase_order, {line.pk: 2}, expected_received=_expected(line))

        early_request.refresh_from_db()
        late_request.refresh_from_db()
        self.assertEqual(early_request.status, RequestStatusChoices.APPROVED)
        self.assertTrue(FulfillmentLink.objects.get(asset_request=early_request).fully_delivered)
        self.assertEqual(late_request.status, RequestStatusChoices.PROCUREMENT)
        self.assertEqual(FulfillmentLink.objects.get(asset_request=late_request).qty_received, 0)

        receive_purchase_order(purchase_order, {line.pk: 2}, expected_received=_expected(line))

        late_request.refresh_from_db()
        self.assertEqual(late_request.status, RequestStatusChoices.APPROVED)
        self.assertEqual(ComponentStock.objects.get(component=component, location=self.location).qty, 4)

    def test_over_receipt_across_partial_receipts_is_refused_and_previous_receipts_survive(self):
        component = self._component("Bounded RAM", "bounded-ram")
        purchase_order = self._draft_purchase_order("PO-PARTIAL-BOUND-001")
        request = self._request(component=component, qty=10)
        link = link_asset_request_to_purchase_order(purchase_order, request.pk, user=self.user)
        line = link.purchase_order_line
        self._open(purchase_order)

        receive_purchase_order(purchase_order, {line.pk: 8}, expected_received=_expected(line))

        with self.assertRaisesMessage(ValidationError, "only 2 remain outstanding"):
            receive_purchase_order(purchase_order, {line.pk: 5}, expected_received=_expected(line))

        line.refresh_from_db()
        link.refresh_from_db()
        request.refresh_from_db()
        self.assertEqual(line.qty_received, 8)
        self.assertEqual(link.qty_received, 8)
        self.assertEqual(link.qty_outstanding, 2)
        self.assertEqual(request.status, RequestStatusChoices.PROCUREMENT)
        self.assertEqual(ComponentStock.objects.get(component=component, location=self.location).qty, 8)

    def test_surplus_receipt_beyond_linked_demand_stays_free_stock(self):
        component = self._component("Surplus RAM", "surplus-ram")
        purchase_order = self._draft_purchase_order("PO-PARTIAL-SURPLUS-001")
        line = PurchaseOrderLine.objects.create(
            tenant=self.tenant,
            purchase_order=purchase_order,
            component=component,
            qty_ordered=10,
            unit_price="5.00",
        )
        self._open(purchase_order)
        request = self._request(component=component, qty=4, status=RequestStatusChoices.PROCUREMENT)
        link = FulfillmentLink.objects.create(
            tenant=self.tenant,
            asset_request=request,
            purchase_order_line=line,
            qty_allocated=4,
            qty_received=0,
        )

        receive_purchase_order(purchase_order, {line.pk: 10}, expected_received=_expected(line))

        request.refresh_from_db()
        link.refresh_from_db()
        self.assertEqual(request.status, RequestStatusChoices.APPROVED)
        self.assertEqual(link.qty_received, 4)
        self.assertTrue(link.fully_delivered)
        self.assertEqual(ComponentStock.objects.get(component=component, location=self.location).qty, 10)

    def test_legacy_over_allocated_serialised_link_never_fabricates_full_delivery(self):
        purchase_order = self._draft_purchase_order("PO-PARTIAL-LEGACY-001")
        line = PurchaseOrderLine.objects.create(
            tenant=self.tenant,
            purchase_order=purchase_order,
            asset_type=self.asset_type,
            qty_ordered=2,
            unit_price="10.00",
        )
        self._open(purchase_order)
        request = self._request(asset_type=self.asset_type, qty=2, status=RequestStatusChoices.PROCUREMENT)
        link = FulfillmentLink.objects.create(
            tenant=self.tenant,
            asset_request=request,
            purchase_order_line=line,
            qty_allocated=2,
            qty_received=None,
        )

        receive_purchase_order(
            purchase_order,
            {line.pk: 1},
            [{"line_id": line.pk, "serial_number": "LEGACY-SN-1", "asset_tag": "LEGACY-TAG-1"}],
            expected_received=_expected(line),
        )

        request.refresh_from_db()
        link.refresh_from_db()
        self.assertIsNotNone(request.asset_id)
        self.assertEqual(request.status, RequestStatusChoices.PROCUREMENT)
        self.assertEqual((link.qty_received, link.qty_outstanding), (1, 1))
        self.assertFalse(link.fully_delivered)
        first_asset_id = request.asset_id

        receive_purchase_order(
            purchase_order,
            {line.pk: 1},
            [{"line_id": line.pk, "serial_number": "LEGACY-SN-2", "asset_tag": "LEGACY-TAG-2"}],
            expected_received=_expected(line),
        )

        request.refresh_from_db()
        link.refresh_from_db()
        self.assertEqual(request.asset_id, first_asset_id)
        self.assertEqual(request.status, RequestStatusChoices.PROCUREMENT)
        self.assertEqual((link.qty_received, link.qty_outstanding), (1, 1))
        self.assertEqual(Asset._base_manager.filter(purchase_order_line=line).count(), 2)

    def test_legacy_untracked_link_completes_on_full_receipt(self):
        component = self._component("Legacy RAM", "legacy-ram")
        purchase_order = self._draft_purchase_order("PO-PARTIAL-LEGACY-002")
        line = PurchaseOrderLine.objects.create(
            tenant=self.tenant,
            purchase_order=purchase_order,
            component=component,
            qty_ordered=5,
            unit_price="5.00",
        )
        self._open(purchase_order)
        request = self._request(component=component, qty=5, status=RequestStatusChoices.PROCUREMENT)
        link = FulfillmentLink.objects.create(
            tenant=self.tenant,
            asset_request=request,
            purchase_order_line=line,
            qty_allocated=5,
            qty_received=None,
        )
        self.assertEqual(link.qty_outstanding, 5)

        receive_purchase_order(purchase_order, {line.pk: 5}, expected_received=_expected(line))

        request.refresh_from_db()
        link.refresh_from_db()
        self.assertEqual(request.status, RequestStatusChoices.APPROVED)
        self.assertEqual(link.qty_received, 5)
        self.assertTrue(link.fully_delivered)

    def test_purchase_order_detail_reports_received_and_outstanding(self):
        component = self._component("Readback RAM", "readback-ram")
        purchase_order = self._draft_purchase_order("PO-PARTIAL-READBACK-001")
        request = self._request(component=component, qty=10)
        link = link_asset_request_to_purchase_order(purchase_order, request.pk, user=self.user)
        line = link.purchase_order_line
        self._open(purchase_order)
        receive_purchase_order(purchase_order, {line.pk: 2}, expected_received=_expected(line))

        self.client.force_login(self.user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant.pk
        session.save()

        order_response = self.client.get(purchase_order.get_absolute_url())
        self.assertEqual(order_response.status_code, 200)
        self.assertContains(order_response, "Outstanding")
        self.assertContains(order_response, "2 / 10")

        request_response = self.client.get(reverse("assets:request_detail", kwargs={"pk": request.pk}))
        self.assertEqual(request_response.status_code, 200)
        self.assertContains(request_response, "Received 2 / 10")

    def test_replayed_receipt_submission_is_refused_without_double_booking(self):
        component = self._component("Replay RAM", "replay-ram")
        purchase_order = self._draft_purchase_order("PO-PARTIAL-REPLAY-001")
        line = PurchaseOrderLine.objects.create(
            tenant=self.tenant,
            purchase_order=purchase_order,
            component=component,
            qty_ordered=10,
            unit_price="5.00",
        )
        self._open(purchase_order)
        request = self._request(component=component, qty=10, status=RequestStatusChoices.PROCUREMENT)
        link = FulfillmentLink.objects.create(
            tenant=self.tenant,
            asset_request=request,
            purchase_order_line=line,
            qty_allocated=10,
            qty_received=0,
        )

        receive_purchase_order(purchase_order, {line.pk: 3}, expected_received={line.pk: 0})

        # Replaying the exact submission after it was applied must not book it a second time.
        with self.assertRaisesMessage(ValidationError, "changed since this receipt was prepared"):
            receive_purchase_order(purchase_order, {line.pk: 3}, expected_received={line.pk: 0})

        line.refresh_from_db()
        link.refresh_from_db()
        request.refresh_from_db()
        self.assertEqual(line.qty_received, 3)
        self.assertEqual(link.qty_received, 3)
        self.assertEqual(request.status, RequestStatusChoices.PROCUREMENT)
        self.assertEqual(ComponentStock.objects.get(component=component, location=self.location).qty, 3)

        # A submission prepared against the current state is a distinct, genuine delivery.
        receive_purchase_order(purchase_order, {line.pk: 3}, expected_received=_expected(line))

        line.refresh_from_db()
        link.refresh_from_db()
        self.assertEqual(line.qty_received, 6)
        self.assertEqual(link.qty_received, 6)
        self.assertEqual(ComponentStock.objects.get(component=component, location=self.location).qty, 6)

    def test_receipt_submission_without_stated_state_is_refused(self):
        component = self._component("Unstated RAM", "unstated-ram")
        purchase_order = self._draft_purchase_order("PO-PARTIAL-UNSTATED-001")
        line = PurchaseOrderLine.objects.create(
            tenant=self.tenant,
            purchase_order=purchase_order,
            component=component,
            qty_ordered=4,
            unit_price="5.00",
        )
        self._open(purchase_order)

        with self.assertRaisesMessage(ValidationError, "does not state the recorded quantity"):
            receive_purchase_order(purchase_order, {line.pk: 2}, expected_received={})

        line.refresh_from_db()
        self.assertEqual(line.qty_received, 0)

    def test_web_receive_form_replay_books_exactly_once(self):
        """The rendered form binds the receipt-state snapshot; replaying its submission books once.

        Regression for the web flow: the snapshot must be captured when the form is prepared
        and never regenerated during POST handling, or an HTTP replay would silently book the
        same delivery again.
        """
        component = self._component("Web Replay RAM", "web-replay-ram")
        purchase_order = self._draft_purchase_order("PO-WEB-REPLAY-001")
        line = PurchaseOrderLine.objects.create(
            tenant=self.tenant,
            purchase_order=purchase_order,
            component=component,
            qty_ordered=10,
            unit_price="5.00",
        )
        self._open(purchase_order)
        request = self._request(component=component, qty=10, status=RequestStatusChoices.PROCUREMENT)
        FulfillmentLink.objects.create(
            tenant=self.tenant,
            asset_request=request,
            purchase_order_line=line,
            qty_allocated=10,
            qty_received=0,
        )

        self.client.force_login(self.user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant.pk
        session.save()

        url = reverse("procurement:purchaseorder_receive_form", kwargs={"pk": purchase_order.pk})
        # Rendering the form is what binds the receipt-state snapshot for the submission below.
        self.assertEqual(self.client.get(url).status_code, 200)

        payload = {
            "step": "1",
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
            "form-0-line_id": str(line.pk),
            "form-0-qty_to_receive": "3",
        }
        first_response = self.client.post(url, payload, follow=True)
        self.assertEqual(first_response.status_code, 200)
        line.refresh_from_db()
        self.assertEqual(line.qty_received, 3)
        self.assertEqual(ComponentStock.objects.get(component=component, location=self.location).qty, 3)

        # Replaying the identical HTTP submission refreshes nothing and books nothing.
        replay_response = self.client.post(url, payload, follow=True)
        self.assertEqual(replay_response.status_code, 200)
        self.assertContains(replay_response, "changed since this receipt was prepared")
        line.refresh_from_db()
        self.assertEqual(line.qty_received, 3)
        self.assertEqual(ComponentStock.objects.get(component=component, location=self.location).qty, 3)

        # Re-rendering the form binds a fresh snapshot; the next submission is a genuine delivery.
        self.assertEqual(self.client.get(url).status_code, 200)
        third_response = self.client.post(url, payload, follow=True)
        self.assertEqual(third_response.status_code, 200)
        line.refresh_from_db()
        self.assertEqual(line.qty_received, 6)
        self.assertEqual(ComponentStock.objects.get(component=component, location=self.location).qty, 6)


class CancellationReleaseTests(PartialReceiptFixture):
    def _cancel_request(self, request):
        self.client.force_login(self.user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant.pk
        session.save()
        return self.client.post(reverse("assets:request_cancel", kwargs={"pk": request.pk}))

    def test_cancelling_request_closes_its_links_and_preserves_attribution(self):
        component = self._component("Cancelled RAM", "cancelled-ram")
        purchase_order = self._draft_purchase_order("PO-PARTIAL-CANCEL-001")
        request = self._request(component=component, qty=10)
        link = link_asset_request_to_purchase_order(purchase_order, request.pk, user=self.user)
        line = link.purchase_order_line
        self._open(purchase_order)
        receive_purchase_order(purchase_order, {line.pk: 3}, expected_received=_expected(line))

        other_component = self._component("Untouched RAM", "untouched-ram")
        other_purchase_order = self._draft_purchase_order("PO-PARTIAL-CANCEL-002")
        other_request = self._request(component=other_component, qty=4)
        other_link = link_asset_request_to_purchase_order(other_purchase_order, other_request.pk, user=self.user)

        response = self._cancel_request(request)

        self.assertEqual(response.status_code, 302)
        request.refresh_from_db()
        self.assertEqual(request.status, RequestStatusChoices.CANCELLED)
        self.assertFalse(FulfillmentLink.objects.filter(asset_request=request).exists())
        closed_link = FulfillmentLink._base_manager.get(asset_request=request)
        self.assertEqual((closed_link.qty_received, closed_link.qty_allocated), (3, 10))
        line.refresh_from_db()
        purchase_order.refresh_from_db()
        self.assertEqual(line.qty_received, 3)
        self.assertEqual(purchase_order.status, PurchaseOrder.STATUS_PARTIAL)
        self.assertEqual(ComponentStock.objects.get(component=component, location=self.location).qty, 3)

        other_request.refresh_from_db()
        self.assertEqual(other_request.status, RequestStatusChoices.PROCUREMENT)
        self.assertTrue(FulfillmentLink.objects.filter(asset_request=other_request).exists())
        self.assertIsInstance(other_link.pk, int)

    def test_cancelling_group_closes_child_links_even_after_partial_receipt(self):
        purchase_order = self._draft_purchase_order("PO-PARTIAL-CANCEL-003")
        parent, children = self._group_with_children(2)
        link = link_asset_request_to_purchase_order(purchase_order, parent.pk, user=self.user)
        line = link.purchase_order_line
        self._open(purchase_order)
        receive_purchase_order(
            purchase_order,
            {line.pk: 1},
            [{"line_id": line.pk, "serial_number": "CANCEL-SN-1", "asset_tag": "CANCEL-TAG-1"}],
            expected_received=_expected(line),
        )
        children[0].refresh_from_db()
        children[1].refresh_from_db()
        self.assertEqual(children[0].status, RequestStatusChoices.APPROVED)
        delivered_asset_id = children[0].asset_id
        self.assertIsNotNone(delivered_asset_id)

        response = self._cancel_request(parent)

        self.assertEqual(response.status_code, 302)
        parent.refresh_from_db()
        self.assertEqual(parent.status, RequestStatusChoices.CANCELLED)
        for child in children:
            child.refresh_from_db()
            self.assertEqual(child.status, RequestStatusChoices.CANCELLED)
            self.assertFalse(FulfillmentLink.objects.filter(asset_request=child).exists())
        children[0].refresh_from_db()
        self.assertEqual(children[0].asset_id, delivered_asset_id)
        self.assertEqual(FulfillmentLink._base_manager.get(asset_request=children[0]).qty_received, 1)
        self.assertEqual(FulfillmentLink._base_manager.get(asset_request=children[1]).qty_received, 0)
        line.refresh_from_db()
        self.assertEqual(line.qty_received, 1)
