import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from model_bakery import baker

from assets.models import Asset, StatusLabel, AssetAssignment
from assets.services import checkout_asset, dispose_asset
from licenses.models import License, LicenseSeatAssignment
from organization.models import AssetHolder


class AssignmentSoftDeleteConstraintTests(TestCase):
    """WS2-4: a soft-deleted assignment (deleted_at set, is_active still True) must not block
    a fresh checkout of the same asset on the partial-unique constraint."""

    def setUp(self):
        self.deployable = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)

    def test_checkout_after_soft_deleting_active_assignment(self):
        asset = baker.make(Asset, status=self.deployable, tenant=None)
        h1 = baker.make(AssetHolder, tenant=None)
        h2 = baker.make(AssetHolder, tenant=None)
        checkout_asset(asset=asset, holder=h1)
        assignment = AssetAssignment.objects.get(asset=asset, is_active=True)
        assignment.delete()  # soft delete: deleted_at set, is_active stays True
        # Must NOT raise IntegrityError on unique_active_assignment_per_asset.
        checkout_asset(asset=asset, holder=h2)
        self.assertEqual(AssetAssignment.objects.filter(asset=asset, is_active=True).count(), 1)


class LicenseSeatsGuardTests(TestCase):
    """WS2-5: License.seats cannot be reduced below the active assignment count."""

    def test_cannot_reduce_seats_below_assigned(self):
        lic = baker.make(License, seats=2, tenant=None)
        a1 = baker.make(Asset, tenant=None)
        a2 = baker.make(Asset, tenant=None)
        LicenseSeatAssignment.objects.create(license=lic, asset=a1)
        LicenseSeatAssignment.objects.create(license=lic, asset=a2)
        lic.seats = 1
        with self.assertRaises(ValidationError):
            lic.full_clean()


class DisposalProceedsGuardTests(TestCase):
    """WS6-4: disposal proceeds must be non-negative and in the asset's own currency."""

    def setUp(self):
        self.deployable = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)

    def test_negative_proceeds_rejected(self):
        asset = baker.make(Asset, status=self.deployable, currency='EUR', tenant=None)
        with self.assertRaises(ValidationError):
            dispose_asset(asset, disposal_method='recycle', disposal_date=datetime.date.today(),
                          proceeds=Decimal('-5'))

    def test_foreign_currency_proceeds_rejected(self):
        asset = baker.make(Asset, status=self.deployable, currency='EUR', tenant=None)
        with self.assertRaises(ValidationError):
            dispose_asset(asset, disposal_method='recycle', disposal_date=datetime.date.today(),
                          proceeds=Decimal('100'), currency='USD')
