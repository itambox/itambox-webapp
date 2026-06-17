"""Security regression (E3): AssetReservation double-booking must be blocked at
the DB layer, not only in clean().

clean() (Python) is defense-in-depth, but concurrent/programmatic .objects.create()
bypasses it. These tests assert the Postgres ExclusionConstraint
(``assetreservation_no_overlap``) rejects overlapping ACTIVE/PENDING reservations
for the same asset directly at the DB, while leaving non-overlapping and
soft-deleted rows unaffected.
"""
import datetime

from django.db import IntegrityError, transaction
from django.db.models.signals import pre_save
from django.test import TestCase
from django.utils import timezone
from model_bakery import baker

from assets.models import (
    Asset, StatusLabel,
    AssetReservation, ReservationStatusChoices,
)
from core.signals import validate_custom_validators_on_save


def _deployable_asset(**kwargs):
    # Unique status label slug/name per E3 suite to avoid colliding with sibling
    # tests in the full (order-dependent) run.
    status = baker.make(StatusLabel, type='deployable', name='Deployable-e3')
    return baker.make(Asset, status=status, tenant=None, **kwargs)


class AssetReservationOverlapConstraintTest(TestCase):

    def setUp(self):
        self.asset = _deployable_asset(name='Reserved Asset E3')
        from organization.models import AssetHolder
        self.holder_a = baker.make(AssetHolder)
        self.holder_b = baker.make(AssetHolder)
        self.today = datetime.date.today()
        # A global pre_save signal (core.signals.validate_custom_validators_on_save)
        # runs clean() on every save, so .save()/.create() would raise the
        # ValidationError from AssetReservation.clean() before reaching the DB.
        # Disconnect it here so these tests exercise the DB-level ExclusionConstraint
        # directly — the layer that protects against the check-then-insert RACE
        # that clean() alone cannot close.
        pre_save.disconnect(validate_custom_validators_on_save)

    def tearDown(self):
        pre_save.connect(validate_custom_validators_on_save)

    def _make(self, *, start_offset, end_offset, status, holder=None, deleted=False):
        """Create a reservation via .objects.create() with clean() disconnected,
        so the only overlap guard in play is the DB ExclusionConstraint."""
        kwargs = dict(
            asset=self.asset,
            reserved_for=holder,
            start_date=self.today + datetime.timedelta(days=start_offset),
            end_date=self.today + datetime.timedelta(days=end_offset),
            status=status,
        )
        if deleted:
            kwargs['deleted_at'] = timezone.now()
        return AssetReservation.objects.create(**kwargs)

    def test_db_constraint_rejects_overlapping_active_reservation(self):
        """A second ACTIVE/PENDING reservation overlapping the same asset's window
        must raise IntegrityError at the DB, even though .create() skips clean()."""
        self._make(
            start_offset=0, end_offset=10,
            status=ReservationStatusChoices.ACTIVE, holder=self.holder_a,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._make(
                    start_offset=5, end_offset=15,
                    status=ReservationStatusChoices.PENDING, holder=self.holder_b,
                )

    def test_db_constraint_allows_non_overlapping_reservation(self):
        """A reservation starting after the first one's last held day must succeed.

        Inclusive '[]' semantics: the first holds days [0, 10] (through day 10),
        so the next reservation must start on day 11 or later. Days 10 and 11 are
        adjacent but not shared, so there is no overlap.
        """
        self._make(
            start_offset=0, end_offset=10,
            status=ReservationStatusChoices.ACTIVE, holder=self.holder_a,
        )
        # Starts the day AFTER the first ends — no shared day, must not raise.
        res2 = self._make(
            start_offset=11, end_offset=20,
            status=ReservationStatusChoices.PENDING, holder=self.holder_b,
        )
        self.assertIsNotNone(res2.pk)

    def test_db_constraint_rejects_boundary_touching_reservation(self):
        """Inclusive end_date: reservations sharing a boundary day conflict.

        The first holds [0, 10]; a second [10, 20] both claim day 10, so the
        exclusion constraint must reject it (one holder per day, no same-day handoff).
        """
        self._make(
            start_offset=0, end_offset=10,
            status=ReservationStatusChoices.ACTIVE, holder=self.holder_a,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._make(
                    start_offset=10, end_offset=20,
                    status=ReservationStatusChoices.PENDING, holder=self.holder_b,
                )

    def test_db_constraint_rejects_same_day_reservations(self):
        """A one-day reservation (start_date == end_date) is a real, exclusive
        booking: two reservations on the same single day must conflict."""
        self._make(
            start_offset=5, end_offset=5,
            status=ReservationStatusChoices.ACTIVE, holder=self.holder_a,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._make(
                    start_offset=5, end_offset=5,
                    status=ReservationStatusChoices.PENDING, holder=self.holder_b,
                )

    def test_db_constraint_ignores_soft_deleted_reservation(self):
        """A soft-deleted reservation must NOT block a new overlapping one (the
        constraint condition excludes deleted_at IS NOT NULL rows)."""
        self._make(
            start_offset=0, end_offset=10,
            status=ReservationStatusChoices.ACTIVE, holder=self.holder_a,
            deleted=True,
        )
        # Overlaps the soft-deleted window, but must be allowed.
        res2 = self._make(
            start_offset=5, end_offset=15,
            status=ReservationStatusChoices.ACTIVE, holder=self.holder_b,
        )
        self.assertIsNotNone(res2.pk)

    def test_db_constraint_ignores_inactive_status(self):
        """CANCELLED/FULFILLED reservations do not participate in the exclusion,
        so an overlapping ACTIVE reservation must still be allowed."""
        self._make(
            start_offset=0, end_offset=10,
            status=ReservationStatusChoices.CANCELLED, holder=self.holder_a,
        )
        res2 = self._make(
            start_offset=5, end_offset=15,
            status=ReservationStatusChoices.ACTIVE, holder=self.holder_b,
        )
        self.assertIsNotNone(res2.pk)
