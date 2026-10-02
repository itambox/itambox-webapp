"""Reservation journeys: a reservation holds the asset against every checkout target, not only people."""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.test import TestCase

from assets.models import AssetAssignment, AssetReservation, ReservationStatusChoices
from assets.services import checkout_asset
from organization.models import Location, Site

from .support import JourneyMixin, today


class ReservationJourneyTests(JourneyMixin, TestCase):
    def setUp(self):
        self.make_tenant("journey-reservation")
        self.asset = self.make_asset()
        self.reserved_for = self.make_holder()
        AssetReservation.objects.create(
            asset=self.asset,
            reserved_for=self.reserved_for,
            start_date=today() - timedelta(days=1),
            end_date=today() + timedelta(days=3),
            status=ReservationStatusChoices.ACTIVE,
        )
        site = Site.objects.create(name="Journey Reservation Site", slug="journey-reservation-site")
        self.location = Location.objects.create(
            name="Journey Reservation Loc", slug="journey-reservation-loc", site=site, tenant=self.tenant
        )

    @pytest.mark.xfail(strict=True, reason="reservation guard only covers holder checkouts (#609)")
    def test_checkout_to_location_during_another_holders_reservation_is_refused(self):
        with self.assertRaises(ValidationError):
            checkout_asset(asset=self.asset, location=self.location, request=None)

        self.assertFalse(AssetAssignment.objects.filter(asset=self.asset, is_active=True).exists())

    def test_checkout_to_another_holder_during_reservation_is_refused(self):
        other = self.make_holder()
        with self.assertRaises(ValidationError):
            checkout_asset(asset=self.asset, holder=other, request=None)
