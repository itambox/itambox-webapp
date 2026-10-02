"""Custody journeys: a receipt is accepted only by the person currently holding the asset."""

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker

from assets.services import checkin_asset, checkout_asset
from compliance.models import CustodyReceipt, CustodyTemplate

from .support import JourneyMixin

User = get_user_model()

SIGNATURE = "journey-signature-payload"


@override_settings(REQUIRE_CUSTODY_SIGNIN=True)
class CustodyReceiptJourneyTests(JourneyMixin, TestCase):
    def setUp(self):
        self.make_tenant("journey-custody")
        category = baker.make("assets.Category", name="Journey Laptops", slug="journey-laptops")
        CustodyTemplate.objects.create(
            name="Journey EULA",
            tenant=self.tenant,
            category=category,
            require_acceptance=True,
            eula_text="journey eula",
        )
        asset_type = baker.make("assets.AssetType", model="JourneyBook", slug="journey-book", category=category)
        self.asset = self.make_asset(asset_type=asset_type, asset_tag="JOURNEY-CUSTODY-1")

    def _sign(self, receipt, **client_kwargs):
        url = reverse("compliance:custody_eula_sign", kwargs={"token": receipt.token})
        return self.client.post(url, {"action": "accept", "signature_canvas": SIGNATURE}, **client_kwargs)

    def _checkout(self, holder):
        checkout_asset(asset=self.asset, holder=holder, checkout_date=timezone.now(), request=None)
        return CustodyReceipt.objects.get(asset=self.asset, holder=holder)

    @pytest.mark.xfail(strict=True, reason="custody receipt outlives its assignment (#607)")
    def test_former_holder_cannot_accept_after_checkin(self):
        user = self.make_member("former-holder", set())
        holder = self.make_holder(user=user)
        receipt = self._checkout(holder)
        checkin_asset(self.asset)

        self.client_login_to_tenant(user, self.tenant)
        self._sign(receipt)

        receipt.refresh_from_db()
        self.assertNotEqual(receipt.acceptance_status, CustodyReceipt.STATUS_ACCEPTED)

    @pytest.mark.xfail(strict=True, reason="custody receipt outlives its assignment (#607)")
    def test_former_holder_cannot_accept_after_reassignment(self):
        first_user = self.make_member("first-holder", set())
        first = self.make_holder(user=first_user)
        second = self.make_holder(user=self.make_member("second-holder", set()))
        receipt = self._checkout(first)
        checkin_asset(self.asset)
        self._checkout(second)

        self.client_login_to_tenant(first_user, self.tenant)
        self._sign(receipt)

        receipt.refresh_from_db()
        self.assertNotEqual(receipt.acceptance_status, CustodyReceipt.STATUS_ACCEPTED)

    @pytest.mark.xfail(strict=True, reason="holder without a tenant membership gets a 500 (#607)")
    def test_holder_without_membership_can_sign_their_receipt(self):
        outsider = User.objects.create_user(username="no-membership", password="x")
        holder = self.make_holder(user=outsider)
        receipt = self._checkout(holder)

        self.client.force_login(outsider)
        response = self._sign(receipt, raise_request_exception=False)

        self.assertEqual(response.status_code, 200)
        receipt.refresh_from_db()
        self.assertEqual(receipt.acceptance_status, CustodyReceipt.STATUS_ACCEPTED)
