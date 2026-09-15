from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from assets.choices import RequestStatusChoices
from assets.models import Asset, AssetAssignment, AssetRequest, StatusLabel
from assets.services import checkin_asset, checkout_asset
from assets.services import request_fulfillment as fulfillment
from assets.services.request_fulfillment import (
    FULFILLMENT_EVIDENCE_KEY,
    complete_request_from_checkout,
    get_request_fulfillment_evidence,
    manually_complete_request,
    request_fulfillment_labels,
)
from assets.tests import test_requests
from assets.tests.test_issue492_fulfillment import _all_accessible_scope
from core.managers import set_current_tenant, set_current_tenant_group
from core.models import ObjectChange
from organization.models import Tenant, TenantGroup


class Issue493FulfillmentTests(TestCase):
    def make_request(self, **kwargs):
        values = dict(
            requester=self.requester_user,
            tenant=self.tenant,
            asset_type=self.type_requestable,
            status=RequestStatusChoices.APPROVED,
        )
        values.update(kwargs)
        obj = AssetRequest(**values)
        obj._skip_duplicate_check = True
        obj.save()
        return obj

    def complete_manually(self, obj):
        return manually_complete_request(
            obj, actor=self.admin, reason="Delivered externally", confirmed_no_handover=True
        )

    def test_manual_completion_in_tenant_group_scope(self):
        group = TenantGroup.objects.create(name="Group493", slug="group493")
        self.tenant.group = group
        self.tenant.save()
        obj = self.make_request()
        set_current_tenant(None)
        set_current_tenant_group(group)
        try:
            self.complete_manually(obj)
            obj.refresh_from_db()
            self.assertEqual(obj.status, RequestStatusChoices.FULFILLED)
            self.assertIn("no handover booked", request_fulfillment_labels([obj])[obj.pk])
        finally:
            set_current_tenant_group(None)
            set_current_tenant(self.tenant)

    def test_manual_completion_in_all_accessible_scope(self):
        obj = self.make_request()
        with _all_accessible_scope(self.requester_user):
            self.complete_manually(obj)
            obj.refresh_from_db()
            self.assertEqual(obj.status, RequestStatusChoices.FULFILLED)
            self.assertIn("no handover booked", request_fulfillment_labels([obj])[obj.pk])

    def test_group_claim_skips_cancelled_unit_and_records_open_handover(self):
        parent = self.make_request(is_group=True, qty=2)
        cancelled = self.make_request(parent=parent, status=RequestStatusChoices.CANCELLED)
        child = self.make_request(parent=parent, asset=self.asset_requestable)
        self.client.force_login(self.requester_user)
        response = self.client.post(reverse("assets:request_claim", args=[parent.pk]))
        self.assertEqual(response.status_code, 302)
        parent.refresh_from_db()
        child.refresh_from_db()
        cancelled.refresh_from_db()
        self.assertEqual(parent.status, RequestStatusChoices.FULFILLED)
        self.assertEqual(child.status, RequestStatusChoices.FULFILLED)
        self.assertEqual(cancelled.status, RequestStatusChoices.CANCELLED)
        self.assertEqual(AssetAssignment.objects.count(), 1)

    def test_group_claim_rejects_empty_or_pending_units_without_checkout(self):
        parent = self.make_request(is_group=True, qty=2)
        self.client.force_login(self.requester_user)
        self.client.post(reverse("assets:request_claim", args=[parent.pk]))
        parent.refresh_from_db()
        self.assertEqual(parent.status, RequestStatusChoices.APPROVED)
        self.make_request(parent=parent, status=RequestStatusChoices.PENDING)
        self.client.post(reverse("assets:request_claim", args=[parent.pk]))
        parent.refresh_from_db()
        self.assertEqual(parent.status, RequestStatusChoices.APPROVED)
        self.assertEqual(AssetAssignment.objects.count(), 0)

    def test_mixed_group_shows_manual_and_checkout_outcomes(self):
        parent = self.make_request(is_group=True, qty=2)
        manual = self.make_request(parent=parent)
        checked = self.make_request(parent=parent, asset=self.asset_requestable)
        with _all_accessible_scope(self.requester_user):
            self.complete_manually(manual)
            checkout_asset(self.asset_requestable, holder=self.holder, user=self.admin)
            parent.refresh_from_db()
            self.assertIn("mixed", request_fulfillment_labels([parent])[parent.pk])
        self.client.force_login(self.admin)
        response = self.client.get(parent.get_absolute_url())
        self.assertContains(response, "mixed")
        self.assertContains(response, "Manually completed")
        self.assertContains(response, "handover recorded")
        checked.refresh_from_db()
        self.assertEqual(checked.status, RequestStatusChoices.FULFILLED)

    def test_wrong_item_and_inactive_checkout_cannot_certify_request(self):
        obj = self.make_request(asset=self.asset_requestable)
        with fulfillment.claim_fulfillment_scope(-1):
            checkout_asset(self.asset_inherited_requestable, holder=self.holder, user=self.admin)
        other_assignment = AssetAssignment.objects.get(asset=self.asset_inherited_requestable, is_active=True)
        with self.assertRaises(ValidationError):
            complete_request_from_checkout(
                obj, actor=self.admin, transactions=[fulfillment.checkout_transaction_reference(other_assignment)]
            )
        with fulfillment.claim_fulfillment_scope(-1):
            checkout_asset(self.asset_requestable, holder=self.holder, user=self.admin)
        assignment = AssetAssignment.objects.get(asset=self.asset_requestable, is_active=True)
        reference = fulfillment.checkout_transaction_reference(assignment)
        checkin_asset(self.asset_requestable, user=self.admin)
        with self.assertRaises(ValidationError):
            complete_request_from_checkout(obj, actor=self.admin, transactions=[reference])
        obj.refresh_from_db()
        self.assertEqual(obj.status, RequestStatusChoices.APPROVED)

    def test_checkout_proof_is_idempotent_but_cannot_be_reused_for_another_unit(self):
        first = self.make_request(asset=self.asset_requestable)
        second = self.make_request(asset=self.asset_requestable)
        checkout_asset(self.asset_requestable, holder=self.holder, user=self.admin)
        reference = get_request_fulfillment_evidence(first)["transactions"]
        count = ObjectChange._base_manager.count()
        complete_request_from_checkout(first, actor=self.admin, transactions=reference)
        self.assertEqual(ObjectChange._base_manager.count(), count)
        with self.assertRaises(ValidationError):
            complete_request_from_checkout(second, actor=self.admin, transactions=reference)
        second.refresh_from_db()
        self.assertEqual(second.status, RequestStatusChoices.APPROVED)

    def test_nonopen_requests_reject_checkout_certification(self):
        for status in (
            RequestStatusChoices.PROCUREMENT,
            RequestStatusChoices.CANCELLED,
            RequestStatusChoices.DENIED,
            RequestStatusChoices.FULFILLED,
        ):
            with self.subTest(status=status):
                obj = self.make_request(status=status)
                with self.assertRaises(ValidationError):
                    complete_request_from_checkout(obj, actor=self.admin, transactions=[])
                obj.refresh_from_db()
                self.assertEqual(obj.status, status)

    def test_manual_validation_rejects_invalid_service_inputs(self):
        obj = self.make_request()
        for values in ({"actor": None}, {"reason": " "}, {"confirmed_no_handover": "true"}):
            kwargs = {"actor": self.admin, "reason": "External", "confirmed_no_handover": True}
            kwargs.update(values)
            with self.subTest(values=values), self.assertRaises(ValidationError):
                manually_complete_request(obj, **kwargs)
        obj.refresh_from_db()
        self.assertEqual(obj.status, RequestStatusChoices.APPROVED)

    def test_status_labels_are_global_reference_data(self):
        self.assertNotIn("tenant", {field.name for field in StatusLabel._meta.fields})
        self.assertTrue(StatusLabel.changelog_global)

    def test_nonexistent_checkout_reference_cannot_certify_request(self):
        obj = self.make_request(asset=self.asset_requestable)
        with self.assertRaises(ValidationError):
            complete_request_from_checkout(
                obj, actor=self.admin, transactions=[{"model": "assets.AssetAssignment", "pk": 999999999}]
            )
        obj.refresh_from_db()
        self.assertEqual(obj.status, RequestStatusChoices.APPROVED)
        self.assertIsNone(get_request_fulfillment_evidence(obj))

    def test_empty_group_rejected_without_mutation(self):
        obj = self.make_request(is_group=True, qty=2)
        with self.assertRaises(ValidationError):
            self.complete_manually(obj)
        obj.refresh_from_db()
        self.assertEqual(obj.status, RequestStatusChoices.APPROVED)

    def test_bulk_receipt_group_selection_expands_units(self):
        parent = self.make_request(is_group=True, qty=2)
        children = [self.make_request(parent=parent), self.make_request(parent=parent)]
        self.client.force_login(self.admin)
        response = self.client.post(reverse("assets:request_bulk_receive"), {"pk": [str(parent.pk)]})
        self.assertEqual(response.status_code, 200)
        ids = [int(form.initial["request_id"]) for form in response.context["formset"]]
        self.assertCountEqual(ids, [child.pk for child in children])

    def test_manual_completion_records_reason_without_handover(self):
        obj = self.make_request(asset=self.asset_requestable, response_notes="Approval note")
        count = AssetAssignment.objects.count()
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("assets:request_mark_fulfilled", args=[obj.pk]),
            {"reason": "External delivery", "confirmed_no_handover": "on"},
        )
        self.assertEqual(response.status_code, 302)
        obj.refresh_from_db()
        self.assertEqual(obj.status, RequestStatusChoices.FULFILLED)
        self.assertEqual(AssetAssignment.objects.count(), count)
        self.assertIn("Approval note", obj.response_notes)
        self.assertEqual(get_request_fulfillment_evidence(obj)["reason"], "External delivery")
        response = self.client.get(obj.get_absolute_url())
        self.assertContains(response, "Manually completed")
        self.assertContains(response, "External delivery")
        response = self.client.get(reverse("assets:request_list"))
        self.assertContains(response, "no handover booked")

    def test_manual_whitespace_and_unconfirmed_posts_do_not_mutate(self):
        obj = self.make_request(asset=self.asset_requestable)
        self.client.force_login(self.admin)
        for data in ({"reason": "  ", "confirmed_no_handover": "on"}, {"reason": "External"}):
            with self.subTest(data=data):
                response = self.client.post(reverse("assets:request_mark_fulfilled", args=[obj.pk]), data)
                self.assertEqual(response.status_code, 200)
                obj.refresh_from_db()
                self.assertEqual(obj.status, RequestStatusChoices.APPROVED)
                self.assertIsNone(get_request_fulfillment_evidence(obj))

    def test_manual_get_and_post_deny_unprivileged_user(self):
        obj = self.make_request(asset=self.asset_requestable)
        self.client.force_login(self.requester_user)
        url = reverse("assets:request_mark_fulfilled", args=[obj.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"reason": "External", "confirmed_no_handover": "on"}).status_code, 403)
        obj.refresh_from_db()
        self.assertEqual(obj.status, RequestStatusChoices.APPROVED)

    def test_manual_group_preserves_terminal_child_and_records_open_unit(self):
        parent = self.make_request(is_group=True, qty=2)
        done = self.make_request(parent=parent, status=RequestStatusChoices.CANCELLED)
        child = self.make_request(parent=parent)
        self.complete_manually(parent)
        parent.refresh_from_db()
        done.refresh_from_db()
        child.refresh_from_db()
        self.assertEqual(parent.status, RequestStatusChoices.FULFILLED)
        self.assertEqual(done.status, RequestStatusChoices.CANCELLED)
        self.assertEqual(get_request_fulfillment_evidence(child)["method"], "manual")
        self.assertIn("no handover booked", request_fulfillment_labels([parent])[parent.pk])

    def test_manual_group_with_pending_child_rolls_back_all_units(self):
        parent = self.make_request(is_group=True, qty=2)
        child = self.make_request(parent=parent)
        self.make_request(parent=parent, status=RequestStatusChoices.PENDING)
        with self.assertRaises(ValidationError):
            self.complete_manually(parent)
        child.refresh_from_db()
        parent.refresh_from_db()
        self.assertEqual(child.status, RequestStatusChoices.APPROVED)
        self.assertEqual(parent.status, RequestStatusChoices.APPROVED)
        self.assertIsNone(get_request_fulfillment_evidence(child))

    def test_manual_audit_failure_rolls_back_completion(self):
        obj = self.make_request()
        with patch("assets.services.request_fulfillment.write_object_change", side_effect=RuntimeError("Audit failed")):
            with self.assertRaises(RuntimeError):
                self.complete_manually(obj)
        obj.refresh_from_db()
        self.assertEqual(obj.status, RequestStatusChoices.APPROVED)
        self.assertIsNone(obj.response_date)

    def test_checkout_audit_failure_rolls_back_assignment(self):
        obj = self.make_request(asset=self.asset_requestable)
        count = AssetAssignment.objects.count()
        with patch("assets.services.request_fulfillment.write_object_change", side_effect=RuntimeError("Audit failed")):
            with self.assertRaises(RuntimeError):
                checkout_asset(self.asset_requestable, holder=self.holder, user=self.admin)
        obj.refresh_from_db()
        self.asset_requestable.refresh_from_db()
        self.assertEqual(obj.status, RequestStatusChoices.APPROVED)
        self.assertEqual(AssetAssignment.objects.count(), count)
        self.assertEqual(self.asset_requestable.status, self.status_deployable)

    def test_claim_does_not_also_complete_older_matching_request(self):
        older = self.make_request(asset=self.asset_requestable)
        intended = self.make_request(asset=self.asset_requestable)
        self.client.force_login(self.requester_user)
        response = self.client.post(reverse("assets:request_claim", args=[intended.pk]))
        self.assertEqual(response.status_code, 302)
        older.refresh_from_db()
        intended.refresh_from_db()
        self.assertEqual(older.status, RequestStatusChoices.APPROVED)
        self.assertEqual(intended.status, RequestStatusChoices.FULFILLED)
        self.assertEqual(AssetAssignment.objects.filter(asset=self.asset_requestable).count(), 1)
        self.assertIsNone(get_request_fulfillment_evidence(older))
        self.assertEqual(get_request_fulfillment_evidence(intended)["method"], "checkout")

    def test_checkin_retains_recorded_completion(self):
        obj = self.make_request(asset=self.asset_requestable)
        checkout_asset(self.asset_requestable, holder=self.holder, user=self.admin)
        obj.refresh_from_db()
        before = get_request_fulfillment_evidence(obj)
        checkin_asset(self.asset_requestable, user=self.admin)
        self.assertEqual(get_request_fulfillment_evidence(obj), before)
        self.assertIn("handover recorded", request_fulfillment_labels([obj])[obj.pk])

    def test_free_text_and_current_assignment_do_not_certify_legacy_completion(self):
        obj = self.make_request(
            asset=self.asset_requestable,
            status=RequestStatusChoices.FULFILLED,
            response_notes='{"_request_fulfillment": {"method": "checkout"}}',
        )
        checkout_asset(self.asset_requestable, holder=self.holder, user=self.admin)
        self.assertIsNone(get_request_fulfillment_evidence(obj))
        self.assertIn("not verified", request_fulfillment_labels([obj])[obj.pk])

    def test_invalid_audit_metadata_is_unverified(self):
        obj = self.make_request()
        self.complete_manually(obj)
        ct = ContentType.objects.get_for_model(AssetRequest)
        change = ObjectChange._base_manager.filter(
            changed_object_type=ct, changed_object_id=obj.pk, postchange_data__has_key=FULFILLMENT_EVIDENCE_KEY
        ).get()
        payload = change.postchange_data
        payload[FULFILLMENT_EVIDENCE_KEY]["method"] = {}
        change.postchange_data = payload
        change.save()
        self.assertIsNone(get_request_fulfillment_evidence(obj))

    def test_evidence_query_count_does_not_grow_per_row(self):
        rows = [self.make_request(status=RequestStatusChoices.FULFILLED) for _ in range(4)]
        ContentType.objects.get_for_model(AssetRequest)
        with self.assertNumQueries(1):
            labels = request_fulfillment_labels(rows)
        self.assertEqual(len(labels), 4)

    def test_manual_foreign_tenant_url_denied(self):
        other = Tenant.objects.create(name="Outside", slug="outside493")
        foreign = self.make_request(tenant=other)
        self.client.force_login(self.requester_user)
        self.role_standard.permissions += ["assets.fulfill_assetrequest"]
        self.role_standard.save()
        url = reverse("assets:request_mark_fulfilled", args=[foreign.pk])
        response = self.client.post(url, {"reason": "External", "confirmed_no_handover": "on"})
        self.assertIn(response.status_code, (403, 404))
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, RequestStatusChoices.APPROVED)

    def test_manual_htmx_requires_confirmation_and_redirects_on_success(self):
        obj = self.make_request()
        self.client.force_login(self.admin)
        url = reverse("assets:request_mark_fulfilled", args=[obj.pk])
        response = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertContains(response, 'id="asset-request-mark-fulfilled-modal"')
        response = self.client.post(url, {"reason": "External", "confirmed_no_handover": "on"}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["HX-Redirect"], obj.get_absolute_url())

    def setUp(self):
        test_requests.RequisitionSystemTestCase.setUp(self)

    def test_bulk_receive_keeps_request_approved_while_awaiting_handover(self):
        request_obj = AssetRequest.objects.create(
            requester=self.requester_user,
            asset_type=self.type_requestable,
            tenant=self.tenant,
            status=RequestStatusChoices.APPROVED,
        )

        self.client.login(username="adminuser", password="password123")
        response = self.client.post(
            reverse("assets:request_bulk_receive"),
            {
                "form-TOTAL_FORMS": "1",
                "form-INITIAL_FORMS": "0",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-request_id": str(request_obj.pk),
                "form-0-asset_tag": "RECEIVED-493",
                "form-0-serial_number": "SERIAL-493",
                "form-0-name": "Received ThinkPad",
                "form-0-status": str(self.status_deployable.pk),
                "form-0-location": str(self.location.pk),
                "form-0-supplier": "",
                "form-0-order_number": "PO-493",
                "form-0-purchase_cost": "100.00",
                "form-0-purchase_date": "2026-09-14",
            },
        )

        self.assertEqual(response.status_code, 302)
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, RequestStatusChoices.APPROVED)
        self.assertIsNotNone(request_obj.asset_id)
        self.assertFalse(AssetAssignment.objects.filter(asset_id=request_obj.asset_id).exists())
        self.assertEqual(request_obj.asset.location_id, self.location.pk)
        assets_before = Asset.objects.count()
        original_asset = request_obj.asset_id
        replay = self.client.post(reverse("assets:request_bulk_receive"), response.wsgi_request.POST)
        self.assertEqual(replay.status_code, 200)
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.asset_id, original_asset)
        self.assertEqual(Asset.objects.count(), assets_before)
        self.assertTrue(
            any(
                "allocated" in str(message).lower() and "handover" in str(message).lower()
                for message in messages.get_messages(response.wsgi_request)
            )
        )

    def test_manual_completion_without_reason_or_confirmation_does_not_mutate_request(self):
        request_obj = AssetRequest.objects.create(
            requester=self.requester_user,
            asset_type=self.type_requestable,
            asset=self.asset_inherited_requestable,
            tenant=self.tenant,
            status=RequestStatusChoices.APPROVED,
        )

        self.client.login(username="adminuser", password="password123")
        response = self.client.post(reverse("assets:request_mark_fulfilled", kwargs={"pk": request_obj.pk}), {})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required.")
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, RequestStatusChoices.APPROVED)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"version": True, "request_id": 7, "method": "manual", "reason": "X"},
        {"version": 1, "request_id": True, "method": "manual", "reason": "X"},
        {"version": 1, "request_id": 8, "method": "manual", "reason": "X"},
        {"version": 1, "request_id": 7, "method": []},
        {"version": 1, "request_id": 7, "method": "manual", "reason": " "},
        {"version": 1, "request_id": 7, "method": "manual", "reason": 3},
        *[
            {"version": 1, "request_id": 7, "method": "checkout", "transactions": refs}
            for refs in (
                None,
                [],
                [None],
                [{"model": []}],
                [{"model": "users.User", "pk": 1}],
                [{"model": "assets.AssetAssignment", "pk": True}],
                [{"model": "assets.AssetAssignment", "pk": 0}],
                [{"model": "assets.AssetAssignment", "pk": 1, "quantity": False}],
                [{"model": "assets.AssetAssignment", "pk": 1, "quantity": 0}],
            )
        ],
    ],
)
def test_malformed_evidence_payload_is_rejected(payload):
    assert not fulfillment._payload_is_valid(payload, 7)


@pytest.mark.parametrize("quantity,observed", [(0, 1), (True, 1), (2, 1), (None, 0)])
def test_checkout_reference_rejects_invalid_or_mismatched_quantity(quantity, observed):
    record = SimpleNamespace(pk=1, qty=observed, _meta=SimpleNamespace(label="inventory.AccessoryAssignment"))
    with pytest.raises(ValidationError):
        fulfillment.checkout_transaction_reference(record, quantity)


def test_checkout_reference_rejects_unpersisted_or_wrong_item():
    with pytest.raises(ValidationError):
        fulfillment.checkout_transaction_reference(SimpleNamespace(pk=None))
    record = SimpleNamespace(
        pk=1,
        _meta=SimpleNamespace(label="inventory.AccessoryAssignment"),
        _item_attr="accessory",
        accessory_id=3,
        qty=1,
    )
    with pytest.raises(ValidationError):
        fulfillment.checkout_transaction_reference(record, expected_item=SimpleNamespace(pk=4))


@pytest.mark.parametrize(
    "statuses,methods,expected",
    [
        ([], [], "not verified"),
        (["approved"], [None], "still open"),
        (["cancelled"], [None], "no handover booked"),
        (["fulfilled"], [None], "not verified"),
        (["fulfilled"], ["checkout"], "all handovers recorded"),
        (["fulfilled"], ["manual"], "Manually completed"),
        (["fulfilled", "fulfilled"], ["manual", "checkout"], "mixed"),
        (["fulfilled", "denied"], ["checkout", None], "terminal units remain"),
    ],
)
def test_group_completion_labels_describe_unit_outcomes(statuses, methods, expected):
    parent = SimpleNamespace(is_group=True, status="fulfilled")
    children = [SimpleNamespace(pk=i, status=status) for i, status in enumerate(statuses)]
    evidence = {i: {"method": method} for i, method in enumerate(methods) if method}
    assert expected in fulfillment.request_fulfillment_label(parent, child_requests=children, child_evidence=evidence)


def test_empty_label_batch_needs_no_database():
    assert fulfillment.request_fulfillment_labels([]) == {}
