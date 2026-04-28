from django.test import TestCase
from model_bakery import baker
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from assets.models import Asset, StatusLabel, AssetAssignment
from assets.services import checkout_asset, checkin_asset

User = get_user_model()


class AssetAssignmentTestCase(TestCase):
    """
    Test suite for polymorphic AssetAssignment mapping and checkout transactions.
    """

    def setUp(self):
        self.user = baker.make(User)
        self.status = baker.make(StatusLabel, type='deployable')

        self.host_laptop = baker.make(
            Asset,
            name="Developer Laptop",
            status=self.status,
            tenant=None
        )
        self.peripheral_monitor = baker.make(
            Asset,
            name="External Monitor",
            status=self.status,
            tenant=None
        )

    def test_checkout_asset_to_asset(self):
        """
        Verify that checking out a peripheral asset to a host asset succeeds
        and populates the assigned_asset ForeignKey correctly.
        """
        target = checkout_asset(
            asset=self.peripheral_monitor,
            asset_target=self.host_laptop,
            user=self.user
        )

        self.assertEqual(target, self.host_laptop)
        self.peripheral_monitor.refresh_from_db()
        self.assertEqual(self.peripheral_monitor.assigned_to, self.host_laptop)

        active_assignment = self.peripheral_monitor.active_assignment
        self.assertIsNotNone(active_assignment)
        self.assertEqual(active_assignment.assigned_asset, self.host_laptop)
        self.assertEqual(active_assignment.assigned_target, self.host_laptop)
        self.assertEqual(active_assignment.assigned_to_type, 'asset')

    def test_checkin_asset_from_asset(self):
        """
        Verify checking in an asset that was checked out to another asset
        reverts its status and clears active assignments.
        """
        checkout_asset(
            asset=self.peripheral_monitor,
            asset_target=self.host_laptop,
            user=self.user
        )

        msg = checkin_asset(asset=self.peripheral_monitor, user=self.user)
        self.assertIn("Checked in from", msg)

        self.peripheral_monitor.refresh_from_db()
        self.assertFalse(self.peripheral_monitor.assignments.filter(is_active=True).exists())
        self.assertIsNone(self.peripheral_monitor.assigned_to)

