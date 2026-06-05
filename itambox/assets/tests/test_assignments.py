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


class AssetTagSequenceTestCase(TestCase):
    def setUp(self):
        from organization.models import Tenant
        from assets.models import Category, AssetType, Manufacturer
        
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        
        self.category_laptop = Category.objects.create(name="Laptops", slug="laptops")
        self.category_server = Category.objects.create(name="Servers", slug="servers")
        
        self.manufacturer = Manufacturer.objects.create(name="Dell", slug="dell")
        
        # Create AssetTypes with categories
        self.asset_type_laptop = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="Laptop Model",
            category=self.category_laptop
        )
        self.asset_type_server = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="Server Model",
            category=self.category_server
        )
        
        self.status, _ = StatusLabel.objects.get_or_create(
            name="Deployable",
            defaults={'type': 'deployable'}
        )

    def test_sequence_resolution_hierarchy(self):
        from assets.models import AssetTagSequence
        
        # 1. Create global default sequence
        AssetTagSequence.all_objects.create(prefix="GLOBAL-", next_value=100, zero_padding=3)
        
        # 2. Create global category sequence (LAPTOP-)
        AssetTagSequence.all_objects.create(prefix="GLAP-", category=self.category_laptop, next_value=200, zero_padding=3)
        
        # 3. Create tenant-specific default sequence (TENANTA-)
        AssetTagSequence.all_objects.create(tenant=self.tenant_a, prefix="TA-", next_value=300, zero_padding=3)
        
        # 4. Create tenant-specific + category-specific sequence (TA-LAP-)
        AssetTagSequence.all_objects.create(tenant=self.tenant_a, category=self.category_laptop, prefix="TALAP-", next_value=400, zero_padding=3)
        
        # --- Test 1: Tenant A + Category Laptop -> should match TALAP-400 ---
        asset_1 = Asset.objects.create(name="Laptop 1", status=self.status, tenant=self.tenant_a, asset_type=self.asset_type_laptop)
        self.assertEqual(asset_1.asset_tag, "TALAP-400")
        
        # --- Test 2: Tenant A + Category Server -> should fall back to TA-300 ---
        asset_2 = Asset.objects.create(name="Server 1", status=self.status, tenant=self.tenant_a, asset_type=self.asset_type_server)
        self.assertEqual(asset_2.asset_tag, "TA-300")
        
        # --- Test 3: Tenant B + Category Laptop -> should fall back to GLAP-200 (since no Tenant B sequence exists) ---
        asset_3 = Asset.objects.create(name="Laptop 2", status=self.status, tenant=self.tenant_b, asset_type=self.asset_type_laptop)
        self.assertEqual(asset_3.asset_tag, "GLAP-200")
        
        # --- Test 4: Tenant B + Category Server -> should fall back to GLOBAL-000001 (auto created) ---
        asset_4 = Asset.objects.create(name="Server 2", status=self.status, tenant=self.tenant_b, asset_type=self.asset_type_server)
        self.assertEqual(asset_4.asset_tag, "ASSET-000001")

    def test_tenant_scoped_tag_uniqueness(self):
        from django.db import IntegrityError, transaction
        
        # Two different tenants can have the same asset tag
        Asset.objects.create(name="Asset A", asset_tag="TAG-100", status=self.status, tenant=self.tenant_a)
        
        try:
            Asset.objects.create(name="Asset B", asset_tag="TAG-100", status=self.status, tenant=self.tenant_b)
        except IntegrityError:
            self.fail("IntegrityError was raised for identical tags in different tenants.")
            
        # Same tenant cannot have duplicate tag
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Asset.objects.create(name="Asset C", asset_tag="TAG-100", status=self.status, tenant=self.tenant_a)
            
        # Global assets cannot have duplicate tag
        Asset.objects.create(name="Asset Global 1", asset_tag="GLOBAL-TAG", status=self.status, tenant=None)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Asset.objects.create(name="Asset Global 2", asset_tag="GLOBAL-TAG", status=self.status, tenant=None)

    def test_form_requires_asset_tag_and_suggests_it(self):
        from assets.forms.asset_form import AssetForm
        from assets.models import AssetTagSequence
        
        # Create global default sequence
        seq = AssetTagSequence.all_objects.create(prefix="FORM-", category=self.category_laptop, next_value=1, zero_padding=3)

        
        # Form validation fails if asset_tag is left blank (not allowed anymore!)
        form_data_blank = {
            'name': 'Test Asset',
            'asset_tag': '',  # Leave blank!
            'status': self.status.pk,
            'asset_type': self.asset_type_laptop.pk,
            'tenant': self.tenant_a.pk,
        }
        form_blank = AssetForm(data=form_data_blank)
        self.assertFalse(form_blank.is_valid())
        self.assertIn('asset_tag', form_blank.errors)
        
        # Check that the form has a help_text displaying the suggested next tag
        self.assertIn("FORM-001", form_blank.fields['asset_tag'].help_text)
        
        # Submit the form with the suggestion:
        form_data_valid = {
            'name': 'Test Asset 2',
            'asset_tag': 'FORM-001',
            'status': self.status.pk,
            'asset_type': self.asset_type_laptop.pk,
            'tenant': self.tenant_a.pk,
        }
        form_valid = AssetForm(data=form_data_valid)
        self.assertTrue(form_valid.is_valid(), form_valid.errors)
        asset = form_valid.save()
        self.assertEqual(asset.asset_tag, "FORM-001")
        
        # Verify that saving the asset incremented the sequence!
        seq.refresh_from_db()
        self.assertEqual(seq.next_value, 2)





