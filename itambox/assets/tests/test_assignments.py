from django.test import TestCase
from model_bakery import baker
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse

from assets.models import Asset, StatusLabel, AssetAssignment
from assets.services import checkout_asset, checkin_asset

User = get_user_model()


class AssetAssignmentTestCase(TestCase):
    """
    Test suite for polymorphic AssetAssignment mapping and checkout transactions.
    """

    def setUp(self):
        self.user = baker.make(User, is_superuser=True, is_staff=True)
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

    def test_checkout_asset_with_custom_status(self):
        """
        Verify checking out an asset with a custom status label of type 'deployed'
        updates the asset status accordingly.
        """
        deployed_status = baker.make(StatusLabel, type='deployed', name="Custom Deployed")
        target = checkout_asset(
            asset=self.peripheral_monitor,
            asset_target=self.host_laptop,
            user=self.user,
            status=deployed_status
        )
        self.peripheral_monitor.refresh_from_db()
        self.assertEqual(self.peripheral_monitor.status, deployed_status)

    def test_checkin_asset_with_custom_status_location_and_date(self):
        """
        Verify checking in an asset with custom status, location, and check-in date.
        """
        import datetime
        from organization.models import Location
        
        deployed_status = baker.make(StatusLabel, type='deployed', name="Currently Deployed")
        target_location = baker.make(Location, name="Main Storage")
        returned_status = baker.make(StatusLabel, type='deployable', name="Returned - Good")
        
        checkout_asset(
            asset=self.peripheral_monitor,
            asset_target=self.host_laptop,
            user=self.user,
            status=deployed_status
        )
        
        checkin_date = datetime.date.today() - datetime.timedelta(days=1)
        checkin_asset(
            asset=self.peripheral_monitor,
            user=self.user,
            status=returned_status,
            location=target_location,
            checkin_date=checkin_date,
            notes="Checked in with custom parameters"
        )
        
        self.peripheral_monitor.refresh_from_db()
        self.assertEqual(self.peripheral_monitor.status, returned_status)
        self.assertEqual(self.peripheral_monitor.location, target_location)
        
        # Verify the closed assignment record
        assignment = self.peripheral_monitor.assignments.order_by('-created_at').first()
        self.assertFalse(assignment.is_active)
        self.assertEqual(assignment.checked_in_at.date(), checkin_date)
        self.assertIn("Checked in with custom parameters", assignment.notes)

    def test_checkout_view_htmx_redirect(self):
        """
        Verify that submitting the checkout form via HTMX returns a response with HX-Redirect header.
        """
        # Log in the user
        self.client.force_login(self.user)
        
        # Create a status label of type 'deployed'
        deployed_status = baker.make(StatusLabel, type='deployed', name="Test Deployed")
        
        # Prepare post data
        url = reverse('assets:asset_checkout_modal', kwargs={'pk': self.peripheral_monitor.pk})
        data = {
            'target_type': 'asset',
            'asset_target': self.host_laptop.pk,
            'status': deployed_status.pk,
            'notes': 'HTMX checkout test'
        }
        
        # Perform HTMX request (adds HTTP_HX_REQUEST header)
        response = self.client.post(url, data, **{'HTTP_HX_REQUEST': 'true'})
        
        # Debug print form errors
        if response.context and 'form' in response.context:
            print("CHECKOUT FORM ERRORS:", response.context['form'].errors)
        
        # Assert response is successful and has HX-Redirect header
        self.assertEqual(response.status_code, 200)
        self.assertIn('HX-Redirect', response.headers)
        self.assertEqual(response.headers['HX-Redirect'], self.peripheral_monitor.get_absolute_url())

    def test_checkin_view_htmx_redirect(self):
        """
        Verify that submitting the checkin form via HTMX returns a response with HX-Redirect header.
        """
        # Log in the user
        self.client.force_login(self.user)
        
        # Check out the asset first
        checkout_asset(
            asset=self.peripheral_monitor,
            asset_target=self.host_laptop,
            user=self.user
        )
        
        # Prepare check-in data
        url = reverse('assets:asset_checkin', kwargs={'pk': self.peripheral_monitor.pk})
        data = {
            'notes': 'HTMX checkin test'
        }
        
        # Perform HTMX request
        response = self.client.post(url, data, **{'HTTP_HX_REQUEST': 'true'})
        
        # Debug print form errors
        if response.context and 'form' in response.context:
            print("CHECKIN FORM ERRORS:", response.context['form'].errors)
        
        # Assert response is successful and has HX-Redirect header
        self.assertEqual(response.status_code, 200)
        self.assertIn('HX-Redirect', response.headers)
        self.assertEqual(response.headers['HX-Redirect'], self.peripheral_monitor.get_absolute_url())

    def test_same_status_type_transition_allowed(self):
        """
        Verify that changing between different status labels of the same meta-type is allowed.
        """
        # Create or fetch two deployed status labels
        reserved_status, _ = StatusLabel.objects.get_or_create(
            name="Reserved",
            defaults={'type': 'deployed'}
        )
        in_use_status, _ = StatusLabel.objects.get_or_create(
            name="In Use",
            defaults={'type': 'deployed'}
        )

        # Set initial status
        self.peripheral_monitor.status = reserved_status
        self.peripheral_monitor.save()

        # Change to other status of same type
        self.peripheral_monitor.status = in_use_status
        
        # This should execute clean() and validate_transition without throwing ValidationError
        try:
            self.peripheral_monitor.full_clean()
            self.peripheral_monitor.save()
        except ValidationError as e:
            self.fail(f"Validation failed unexpectedly on same-type transition: {e}")

        self.peripheral_monitor.refresh_from_db()
        self.assertEqual(self.peripheral_monitor.status, in_use_status)


