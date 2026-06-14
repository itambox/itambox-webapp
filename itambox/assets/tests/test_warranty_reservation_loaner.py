"""
Tests for:
  - Warranty model (Task 1)
  - AssetReservation model + checkout guard (Task 2)
  - AssetAssignment loaner fields + is_overdue + service wiring (Task 3)
  - Asset.cost_center FK (Task 4)
"""
import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase
from model_bakery import baker

from assets.models import (
    Asset, AssetAssignment, StatusLabel,
    Warranty, WarrantyTypeChoices,
    AssetReservation, ReservationStatusChoices,
)
from assets.services import checkout_asset, checkin_asset


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deployable_asset(**kwargs):
    status = baker.make(StatusLabel, type='deployable')
    return baker.make(Asset, status=status, tenant=None, **kwargs)


# ---------------------------------------------------------------------------
# Task 1 — Warranty model
# ---------------------------------------------------------------------------

class WarrantyModelTest(TestCase):

    def setUp(self):
        self.asset = _deployable_asset(name='Test Asset')

    def test_is_active_within_range(self):
        today = datetime.date.today()
        w = baker.make(
            Warranty,
            asset=self.asset,
            start_date=today - datetime.timedelta(days=10),
            end_date=today + datetime.timedelta(days=10),
        )
        self.assertTrue(w.is_active)

    def test_is_active_expired(self):
        today = datetime.date.today()
        w = baker.make(
            Warranty,
            asset=self.asset,
            start_date=today - datetime.timedelta(days=20),
            end_date=today - datetime.timedelta(days=1),
        )
        self.assertFalse(w.is_active)

    def test_is_active_not_yet_started(self):
        today = datetime.date.today()
        w = baker.make(
            Warranty,
            asset=self.asset,
            start_date=today + datetime.timedelta(days=5),
            end_date=today + datetime.timedelta(days=100),
        )
        self.assertFalse(w.is_active)

    def test_current_warranty_end_returns_max_active(self):
        today = datetime.date.today()
        # Two active warranties; current_warranty_end should return the later one
        baker.make(
            Warranty,
            asset=self.asset,
            start_date=today - datetime.timedelta(days=5),
            end_date=today + datetime.timedelta(days=30),
        )
        baker.make(
            Warranty,
            asset=self.asset,
            start_date=today - datetime.timedelta(days=5),
            end_date=today + datetime.timedelta(days=90),
        )
        self.assertEqual(
            self.asset.current_warranty_end,
            today + datetime.timedelta(days=90),
        )

    def test_current_warranty_end_none_when_no_active(self):
        today = datetime.date.today()
        baker.make(
            Warranty,
            asset=self.asset,
            start_date=today - datetime.timedelta(days=30),
            end_date=today - datetime.timedelta(days=1),
        )
        self.assertIsNone(self.asset.current_warranty_end)

    def test_check_constraint_end_before_start_raises(self):
        """DB-level check constraint: end_date >= start_date."""
        today = datetime.date.today()
        from django.db import IntegrityError, transaction
        with self.assertRaises((IntegrityError, ValidationError)):
            with transaction.atomic():
                Warranty.objects.create(
                    asset=self.asset,
                    warranty_type=WarrantyTypeChoices.HARDWARE,
                    start_date=today,
                    end_date=today - datetime.timedelta(days=1),
                )

    def test_str(self):
        today = datetime.date.today()
        w = baker.make(
            Warranty,
            asset=self.asset,
            warranty_type=WarrantyTypeChoices.HARDWARE,
            start_date=today,
            end_date=today + datetime.timedelta(days=365),
        )
        self.assertIn('Hardware', str(w))
        self.assertIn(self.asset.name, str(w))

    def test_get_absolute_url(self):
        today = datetime.date.today()
        w = baker.make(
            Warranty,
            asset=self.asset,
            start_date=today,
            end_date=today + datetime.timedelta(days=365),
        )
        url = w.get_absolute_url()
        self.assertIn(str(w.pk), url)
        self.assertIn('warranties', url)


# ---------------------------------------------------------------------------
# Task 2 — AssetReservation
# ---------------------------------------------------------------------------

class AssetReservationModelTest(TestCase):

    def setUp(self):
        self.asset = _deployable_asset(name='Reserved Asset')
        from organization.models import AssetHolder
        self.holder_a = baker.make(AssetHolder)
        self.holder_b = baker.make(AssetHolder)

    def test_clean_rejects_overlapping_active_reservation(self):
        today = datetime.date.today()
        # First reservation: active, covers next 10 days
        baker.make(
            AssetReservation,
            asset=self.asset,
            reserved_for=self.holder_a,
            start_date=today,
            end_date=today + datetime.timedelta(days=10),
            status=ReservationStatusChoices.ACTIVE,
        )
        # Second reservation overlaps
        res2 = AssetReservation(
            asset=self.asset,
            reserved_for=self.holder_b,
            start_date=today + datetime.timedelta(days=5),
            end_date=today + datetime.timedelta(days=15),
            status=ReservationStatusChoices.PENDING,
        )
        with self.assertRaises(ValidationError):
            res2.clean()

    def test_clean_allows_non_overlapping_reservations(self):
        today = datetime.date.today()
        baker.make(
            AssetReservation,
            asset=self.asset,
            reserved_for=self.holder_a,
            start_date=today,
            end_date=today + datetime.timedelta(days=5),
            status=ReservationStatusChoices.ACTIVE,
        )
        # Starts after first one ends — should not raise
        res2 = AssetReservation(
            asset=self.asset,
            reserved_for=self.holder_b,
            start_date=today + datetime.timedelta(days=6),
            end_date=today + datetime.timedelta(days=15),
            status=ReservationStatusChoices.PENDING,
        )
        try:
            res2.clean()
        except ValidationError:
            self.fail('clean() raised ValidationError for non-overlapping reservation')

    def test_clean_rejects_end_before_start(self):
        today = datetime.date.today()
        res = AssetReservation(
            asset=self.asset,
            start_date=today + datetime.timedelta(days=5),
            end_date=today,
            status=ReservationStatusChoices.PENDING,
        )
        with self.assertRaises(ValidationError):
            res.clean()

    def test_checkout_blocked_by_reservation_for_different_holder(self):
        """checkout_asset() must raise when a current window reservation exists for a different holder."""
        today = datetime.date.today()
        from django.contrib.auth import get_user_model
        user = baker.make(get_user_model(), is_superuser=True)

        baker.make(
            AssetReservation,
            asset=self.asset,
            reserved_for=self.holder_a,
            start_date=today,
            end_date=today + datetime.timedelta(days=5),
            status=ReservationStatusChoices.ACTIVE,
        )

        with self.assertRaises(ValidationError):
            checkout_asset(
                asset=self.asset,
                holder=self.holder_b,
                user=user,
            )

    def test_checkout_allowed_for_reserved_holder(self):
        """checkout_asset() should succeed when the holder matches the reservation."""
        today = datetime.date.today()
        from django.contrib.auth import get_user_model
        user = baker.make(get_user_model(), is_superuser=True)

        baker.make(
            AssetReservation,
            asset=self.asset,
            reserved_for=self.holder_a,
            start_date=today,
            end_date=today + datetime.timedelta(days=5),
            status=ReservationStatusChoices.ACTIVE,
        )

        target = checkout_asset(
            asset=self.asset,
            holder=self.holder_a,
            user=user,
        )
        self.assertEqual(target, self.holder_a)


# ---------------------------------------------------------------------------
# Task 3 — Loaner fields on AssetAssignment
# ---------------------------------------------------------------------------

class LoanerAssignmentTest(TestCase):

    def setUp(self):
        from django.contrib.auth import get_user_model
        self.user = baker.make(get_user_model(), is_superuser=True)
        self.asset = _deployable_asset(name='Loaner Laptop')
        from organization.models import AssetHolder
        self.holder = baker.make(AssetHolder)

    def test_checkout_with_loan_flag(self):
        today = datetime.date.today()
        due = today + datetime.timedelta(days=7)

        checkout_asset(
            asset=self.asset,
            holder=self.holder,
            user=self.user,
            is_loan=True,
            due_date=due,
        )

        assignment = self.asset.active_assignment
        self.assertIsNotNone(assignment)
        self.assertTrue(assignment.is_loan)
        self.assertEqual(assignment.due_date, due)
        self.assertIsNone(assignment.returned_at)

    def test_is_overdue_true(self):
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        checkout_asset(
            asset=self.asset,
            holder=self.holder,
            user=self.user,
            is_loan=True,
            due_date=yesterday,
        )
        assignment = self.asset.active_assignment
        self.assertTrue(assignment.is_overdue)

    def test_is_overdue_false_when_not_past_due(self):
        future = datetime.date.today() + datetime.timedelta(days=7)
        checkout_asset(
            asset=self.asset,
            holder=self.holder,
            user=self.user,
            is_loan=True,
            due_date=future,
        )
        assignment = self.asset.active_assignment
        self.assertFalse(assignment.is_overdue)

    def test_is_overdue_false_when_not_a_loan(self):
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        checkout_asset(
            asset=self.asset,
            holder=self.holder,
            user=self.user,
            is_loan=False,
            due_date=yesterday,
        )
        assignment = self.asset.active_assignment
        self.assertFalse(assignment.is_overdue)

    def test_checkin_sets_returned_at(self):
        future = datetime.date.today() + datetime.timedelta(days=7)
        checkout_asset(
            asset=self.asset,
            holder=self.holder,
            user=self.user,
            is_loan=True,
            due_date=future,
        )
        assignment = self.asset.active_assignment
        self.assertIsNone(assignment.returned_at)

        checkin_asset(asset=self.asset, user=self.user)

        assignment.refresh_from_db()
        self.assertIsNotNone(assignment.returned_at)
        self.assertFalse(assignment.is_overdue)

    def test_is_overdue_false_after_return(self):
        """Once returned_at is set is_overdue must be False regardless of due_date."""
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        checkout_asset(
            asset=self.asset,
            holder=self.holder,
            user=self.user,
            is_loan=True,
            due_date=yesterday,
        )
        assignment = self.asset.active_assignment
        checkin_asset(asset=self.asset, user=self.user)
        assignment.refresh_from_db()
        self.assertFalse(assignment.is_overdue)


# ---------------------------------------------------------------------------
# Task 4 — Asset.cost_center FK
# ---------------------------------------------------------------------------

class AssetCostCenterTest(TestCase):

    def test_cost_center_nullable(self):
        asset = _deployable_asset(name='CC Asset')
        self.assertIsNone(asset.cost_center)

    def test_cost_center_assignment(self):
        from organization.models import CostCenter
        cc = baker.make(CostCenter)
        asset = _deployable_asset(name='CC Asset 2')
        asset.cost_center = cc
        asset.save(update_fields=['cost_center'])
        asset.refresh_from_db()
        self.assertEqual(asset.cost_center, cc)

    def test_cost_center_set_null_on_delete(self):
        from organization.models import CostCenter
        cc = baker.make(CostCenter)
        asset = _deployable_asset(name='CC Asset 3')
        asset.cost_center = cc
        asset.save(update_fields=['cost_center'])
        # SET_NULL fires on an actual row deletion. CostCenter is soft-delete,
        # so a plain delete() keeps the row (and the FK still resolves) — hard
        # delete to exercise the on_delete=SET_NULL behaviour.
        cc.delete(force_hard_delete=True)
        asset.refresh_from_db()
        self.assertIsNone(asset.cost_center)
