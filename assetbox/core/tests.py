from django.test import TestCase, RequestFactory
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils import timezone
from assetbox.middleware import CurrentUserMiddleware, get_current_user, get_current_request_id
from core.utils import serialize_object
from core.models import ObjectChange
from assets.models import Manufacturer, AssetRole, Asset
from inventory.models import Accessory
from licenses.models import License, LicenseSeatAssignment
from software.models import Software
from users.views import UserPreferencesView

User = get_user_model()

class CoreRefactoringTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username='testuser', password='password123', is_superuser=True)
        self.manufacturer = Manufacturer.objects.create(name='Microsoft', slug='microsoft')
        self.software = Software.objects.create(name='Windows 11', manufacturer=self.manufacturer)

    def test_current_user_middleware_contextvars(self):
        """Test that CurrentUserMiddleware correctly sets and cleans up request user and request ID."""
        request = self.factory.get('/')
        request.user = self.user
        
        middleware = CurrentUserMiddleware(get_response=lambda r: None)
        middleware.process_request(request)
        
        self.assertEqual(get_current_user(), self.user)
        self.assertIsNotNone(get_current_request_id())
        
        response = middleware.process_response(request, None)
        self.assertIsNone(get_current_user())
        self.assertIsNone(get_current_request_id())

    def test_serialize_object_exclude_fields(self):
        """Test that serialize_object respects exclude_fields parameter."""
        data_all = serialize_object(self.software)
        self.assertIn('name', data_all)
        self.assertIn('manufacturer', data_all)
        
        data_excluded = serialize_object(self.software, exclude_fields={'name'})
        self.assertNotIn('name', data_excluded)
        self.assertIn('manufacturer', data_excluded)

    def test_change_logging_no_redundant_logs(self):
        """Test that saving a model instance without changes does not create a redundant ObjectChange log."""
        request = self.factory.get('/')
        request.user = self.user
        middleware = CurrentUserMiddleware(get_response=lambda r: None)
        middleware.process_request(request)
        
        # Initial save creates a CREATE log
        initial_logs_count = ObjectChange.objects.count()
        
        # Second save with no field changes
        self.software.save()
        
        # Should not create another ObjectChange log since nothing changed
        self.assertEqual(ObjectChange.objects.count(), initial_logs_count)
        
        # Cleanup
        middleware.process_response(request, None)

    def test_license_n_plus_one_optimization(self):
        """Test License custom manager with_counts() and available_seats properties."""
        license_obj = License.objects.create(
            name='Office 365 ProPlus',
            software=self.software,
            seats=5
        )
        
        role = AssetRole.objects.create(name='Workstation', slug='workstation')
        asset1 = Asset.objects.create(name='Workstation-01', asset_tag='TAG-01', asset_role=role)
        asset2 = Asset.objects.create(name='Workstation-02', asset_tag='TAG-02', asset_role=role)
        
        LicenseSeatAssignment.objects.create(license=license_obj, asset=asset1)
        LicenseSeatAssignment.objects.create(license=license_obj, asset=asset2)
        
        # Load license without annotation
        license_std = License.objects.get(pk=license_obj.pk)
        self.assertFalse(hasattr(license_std, 'assigned_count'))
        self.assertEqual(license_std.available_seats, 3)
        
        # Load license with annotation
        license_annotated = License.objects.with_counts().get(pk=license_obj.pk)
        self.assertTrue(hasattr(license_annotated, 'assigned_count'))
        self.assertEqual(license_annotated.assigned_count, 2)
        self.assertEqual(license_annotated.available_seats, 3)

    def test_user_preferences_view_mro_inheritance(self):
        """Test that UserPreferencesView has BaseHTMXView in MRO before TemplateResponseMixin."""
        mro = UserPreferencesView.__mro__
        
        # Find their absolute positions in MRO
        import core.views
        import django.views.generic.base
        
        pos_base_htmx = [i for i, cls in enumerate(mro) if cls.__name__ == 'BaseHTMXView'][0]
        pos_template_mixin = [i for i, cls in enumerate(mro) if cls.__name__ == 'TemplateResponseMixin'][0]
        
        self.assertLess(pos_base_htmx, pos_template_mixin, "BaseHTMXView must precede TemplateResponseMixin in MRO")

    def test_htmx_boosted_request_handling(self):
        """Test that HTMX boosted requests correctly swap the base template and return required fragments."""
        self.client.force_login(self.user)
        
        # 1. Normal GET request (Non-HTMX)
        url = reverse('organization:tenant_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<form', response.content)
        self.assertNotIn(b'hx-swap-oob="true"', response.content)
        
        # 2. HTMX Boosted GET request with HX-Target set to 'page-body-main' (without prefix)
        headers = {
            'HTTP_HX_Request': 'true',
            'HTTP_HX_Target': 'page-body-main',
        }
        response = self.client.get(url, **headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context.get('base_template'), 'base_htmx.html')
        self.assertIn(b'<form', response.content)
        # Check that out-of-band swaps for page elements are present
        self.assertIn(b'hx-swap-oob="true"', response.content)
        self.assertIn(b'id="page-title-block"', response.content)
        self.assertIn(b'id="breadcrumbs-block"', response.content)

    def test_change_logging_create_on_save(self):
        request = self.factory.get('/')
        request.user = self.user
        middleware = CurrentUserMiddleware(get_response=lambda r: None)
        middleware.process_request(request)

        initial_count = ObjectChange.objects.count()
        Manufacturer.objects.create(name='Dell', slug='dell-change')

        self.assertGreater(ObjectChange.objects.count(), initial_count)
        change = ObjectChange.objects.latest('time')
        self.assertEqual(change.action, 'create')

        middleware.process_response(request, None)

    def test_change_logging_update_on_save(self):
        request = self.factory.get('/')
        request.user = self.user
        middleware = CurrentUserMiddleware(get_response=lambda r: None)
        middleware.process_request(request)

        mfr = Manufacturer.objects.create(name='HP-update', slug='hp-update')
        ObjectChange.objects.all().delete()

        mfr.name = 'HP Inc'
        mfr.save()

        self.assertEqual(ObjectChange.objects.count(), 1)
        change = ObjectChange.objects.latest('time')
        self.assertEqual(change.action, 'update')

        middleware.process_response(request, None)

    def test_change_logging_delete_on_delete(self):
        request = self.factory.get('/')
        request.user = self.user
        middleware = CurrentUserMiddleware(get_response=lambda r: None)
        middleware.process_request(request)

        mfr = Manufacturer.objects.create(name='Lenovo-del', slug='lenovo-del')
        ObjectChange.objects.all().delete()
        mfr.delete()

        self.assertEqual(ObjectChange.objects.count(), 1)
        change = ObjectChange.objects.latest('time')
        self.assertEqual(change.action, 'delete')

        middleware.process_response(request, None)

    def test_notification_creation(self):
        from core.models import Notification
        notif = Notification.objects.create(
            user=self.user,
            message='This is a test notification',
        )
        self.assertFalse(notif.is_read)

    def test_bookmark_creation(self):
        from core.models import Bookmark
        maker_ct = ContentType.objects.get_for_model(self.manufacturer)
        bookmark = Bookmark.objects.create(
            user=self.user,
            model=maker_ct,
            object_id=self.manufacturer.pk,
        )
        self.assertEqual(bookmark.user, self.user)

    def test_journal_entry_creation(self):
        from core.models import JournalEntry
        maker_ct = ContentType.objects.get_for_model(self.manufacturer)
        entry = JournalEntry.objects.create(
            comment='Test journal note',
            user=self.user,
            model=maker_ct,
            object_id=self.manufacturer.pk,
        )
        self.assertIsNotNone(entry.created_at)

    def test_serialize_object_with_fk(self):
        data = serialize_object(self.software)
        self.assertEqual(data['name'], 'Windows 11')
        self.assertEqual(data['manufacturer'], self.manufacturer.pk)
        self.assertIn('description', data)

    def test_soft_delete_accessory(self):
        from inventory.models import Accessory
        acc = Accessory.objects.create(name='Test Accessory', manufacturer=self.manufacturer)
        acc.delete()
        self.assertIsNotNone(acc.deleted_at)
        self.assertEqual(Accessory.objects.filter(pk=acc.pk).count(), 0)

    def test_cascade_soft_delete_and_hard_delete(self):
        """Test that cascading soft-delete soft-deletes soft-deletable objects and hard-deletes non-soft-deletable ones."""
        from assets.models import InstalledSoftware
        role = AssetRole.objects.create(name='Desktop', slug='desktop')
        asset = Asset.objects.create(name='Test Desktop', asset_tag='TAG-CSD', asset_role=role)
        
        installed_sw = InstalledSoftware.objects.create(
            asset=asset,
            software=self.software,
            version_detected='1.0.0'
        )
        
        self.assertEqual(InstalledSoftware.objects.filter(pk=installed_sw.pk).count(), 1)
        
        asset.delete()
        
        self.assertIsNotNone(asset.deleted_at)
        self.assertEqual(InstalledSoftware.objects.filter(pk=installed_sw.pk).count(), 0)

    def test_manual_checkout_date_backdating(self):
        """Test that custom checkout date can be manually backdated."""
        from assets.services import checkout_asset
        from organization.models import Location, Site
        
        site = Site.objects.create(name='Test Site', slug='test-site')
        location = Location.objects.create(name='Test Location', slug='test-location', site=site)
        
        role = AssetRole.objects.create(name='Desktop', slug='desktop-cod')
        asset = Asset.objects.create(name='Test Asset', asset_tag='TAG-COD', asset_role=role)
        
        custom_date = timezone.now() - timezone.timedelta(days=10)
        
        checkout_asset(
            asset=asset,
            location=location,
            user=self.user,
            checkout_date=custom_date
        )
        
        assignment = asset.active_assignment
        self.assertIsNotNone(assignment)
        self.assertEqual(assignment.checked_out_at, custom_date)

    def test_assignee_column_cache_build(self):
        """Test that AssigneeColumn build cache resolves without crashing for ForeignKey relationships."""
        from core.tables import AssigneeColumn
        from assets.models import AssetAssignment
        from organization.models import Site, Location
        from assets.tables import AssetTable
        
        site = Site.objects.create(name='Test Site 2', slug='test-site-2')
        location = Location.objects.create(name='Test Location 2', slug='test-location-2', site=site)
        
        role = AssetRole.objects.create(name='Desktop', slug='desktop-ac')
        asset = Asset.objects.create(name='Test Asset 2', asset_tag='TAG-AC', asset_role=role)
        
        AssetAssignment.objects.create(
            asset=asset,
            assigned_to=location,
            checked_out_by=self.user
        )
        
        column = AssigneeColumn(assignment_model_path='assets.AssetAssignment')
        table = AssetTable(Asset.objects.filter(pk=asset.pk))
        
        cache_attr = '_assignee_cache_test'
        column._build_cache(table, Asset, cache_attr)
        cache = getattr(table, cache_attr)
        
        self.assertIn(asset.pk, cache)
        self.assertEqual(cache[asset.pk], location)

    def test_assignee_column_render(self):
        """Test that AssigneeColumn renders correctly for checked-out location assets and available assets."""
        from core.tables import AssigneeColumn
        from assets.models import AssetAssignment
        from organization.models import Site, Location
        from assets.tables import AssetTable

        site = Site.objects.create(name='Test Site 3', slug='test-site-3')
        location = Location.objects.create(name='Test Location 3', slug='test-location-3', site=site)

        role = AssetRole.objects.create(name='Desktop', slug='desktop-ar')
        
        # Asset 1: checked out to location
        asset_checked_out = Asset.objects.create(name='Asset Checked Out', asset_tag='TAG-AR1', asset_role=role, location=location)
        AssetAssignment.objects.create(
            asset=asset_checked_out,
            assigned_to=location,
            checked_out_by=self.user
        )

        # Asset 2: available, physical location is location
        asset_available = Asset.objects.create(name='Asset Available', asset_tag='TAG-AR2', asset_role=role, location=location)

        column = AssigneeColumn(location_field='location', assignment_model_path='assets.AssetAssignment')
        table = AssetTable(Asset.objects.filter(pk__in=[asset_checked_out.pk, asset_available.pk]))

        # Render check for Asset 1 (checked out to location)
        col_bound = table.columns['assignee']
        rendered_1 = column.render(asset_checked_out.pk, asset_checked_out, col_bound, table)
        self.assertIn(f'Location: <a href="{location.get_absolute_url()}">{location.name}</a>', rendered_1)

        # Render check for Asset 2 (available)
        rendered_2 = column.render(asset_available.pk, asset_available, col_bound, table)
        self.assertEqual(rendered_2, column.EMPTY_MARK)

    def test_custom_validator_integration(self):
        """Test settings-based validation on a model and verify it raises friendly field-level validation errors."""
        from django.test import override_settings
        
        custom_validators = {
            'assets.manufacturer': {
                'name': {
                    'min_length': 5,
                    'pattern': '^[A-Z].*$'
                }
            }
        }
        
        with override_settings(CUSTOM_VALIDATORS=custom_validators):
            mfr_invalid_len = Manufacturer(name='Abc', slug='abc')
            with self.assertRaises(ValidationError) as ctx:
                mfr_invalid_len.full_clean()
            self.assertIn('name', ctx.exception.error_dict)
            
            mfr_invalid_pattern = Manufacturer(name='lhp-corp', slug='lhp-corp')
            with self.assertRaises(ValidationError) as ctx:
                mfr_invalid_pattern.full_clean()
            self.assertIn('name', ctx.exception.error_dict)
            
            mfr_valid = Manufacturer(name='Lenovo', slug='lenovo-valid')
            mfr_valid.full_clean()

    def test_multi_key_encryption_consolidation(self):
        """Test encryption key rotation using MultiFernet."""
        from core.crypto import encrypt_string, decrypt_string
        from cryptography.fernet import Fernet
        import os
        
        key1 = Fernet.generate_key().decode('ascii')
        key2 = Fernet.generate_key().decode('ascii')
        
        os.environ['ASSETBOX_FIELD_ENCRYPTION_KEYS'] = f"{key1},{key2}"
        plain = "SuperSecretToken"
        cipher = encrypt_string(plain)
        
        decrypted = decrypt_string(cipher)
        self.assertEqual(decrypted, plain)
        
        os.environ['ASSETBOX_FIELD_ENCRYPTION_KEYS'] = f"{key2},{key1}"
        decrypted_rotated = decrypt_string(cipher)
        self.assertEqual(decrypted_rotated, plain)
        
        if 'ASSETBOX_FIELD_ENCRYPTION_KEYS' in os.environ:
            del os.environ['ASSETBOX_FIELD_ENCRYPTION_KEYS']


class RotateEncryptionKeysCommandTest(TestCase):
    def test_rotate_encryption_keys_command(self):
        """Test that the rotate_encryption_keys management command successfully decrypts with old key and re-encrypts with new primary key."""
        from django.core.management import call_command
        from core.crypto import encrypt_string
        from licenses.models import License
        from software.models import Software
        from cryptography.fernet import Fernet
        import os
        
        # 1. Create a software catalog item and a license
        from assets.models import Manufacturer
        mfr = Manufacturer.objects.create(name="Microsoft", slug="microsoft")
        software = Software.objects.create(name="Office 365", version="v2026", manufacturer=mfr)
        
        # 2. Encrypt a product key with old key
        old_key = Fernet.generate_key().decode('ascii')
        new_key = Fernet.generate_key().decode('ascii')
        
        # Set old key as primary/only key
        os.environ['ASSETBOX_FIELD_ENCRYPTION_KEYS'] = old_key
        raw_product_key = "MICROSOFT-OFFICE-KEY-2026"
        
        license_obj = License.objects.create(
            name="Office Suite",
            software=software,
            seats=10,
            product_key=encrypt_string(raw_product_key)
        )
        
        # Verify it encrypted correctly with the old key
        self.assertTrue(license_obj.product_key.startswith("enc$"))
        
        # 3. Rotate key in settings (new key is primary, old key is fallback)
        os.environ['ASSETBOX_FIELD_ENCRYPTION_KEYS'] = f"{new_key},{old_key}"
        
        # 4. Call rotate_encryption_keys command
        call_command('rotate_encryption_keys')
        
        # Refresh from db
        license_obj.refresh_from_db()
        
        # Decrypted value should still be correct
        self.assertEqual(license_obj.decrypted_product_key, raw_product_key)
        
        # Product key in db should now be encrypted using new key (which is different from old key's ciphertext)
        # We can verify this by checking that decrypting the ciphertext with ONLY the new key succeeds!
        os.environ['ASSETBOX_FIELD_ENCRYPTION_KEYS'] = new_key
        self.assertEqual(license_obj.decrypted_product_key, raw_product_key)
        
        # Clean up environment variables
        if 'ASSETBOX_FIELD_ENCRYPTION_KEYS' in os.environ:
            del os.environ['ASSETBOX_FIELD_ENCRYPTION_KEYS']


