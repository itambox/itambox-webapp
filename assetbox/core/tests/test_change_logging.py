import uuid
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from assetbox.middleware import CurrentUserMiddleware, _request_id, _current_user
from core.models import ObjectChange
from assets.models import Manufacturer, AssetRole, Asset, StatusLabel
from organization.models import AssetHolder, Site, Location
from software.models import Software

User = get_user_model()

class ChangeLoggingTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username='testuser', password='password123', is_superuser=True)
        self.manufacturer = Manufacturer.objects.create(name='Microsoft', slug='microsoft')
        self.software = Software.objects.create(name='Windows 11', manufacturer=self.manufacturer)

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

    def test_asset_checkout_status_change_logged(self):
        """Test that asset checkout changes status to 'In Use' and creates a correct ObjectChange record."""
        from assets.services import checkout_asset

        # Ensure Available and In Use status labels exist
        available_status, _ = StatusLabel.objects.get_or_create(
            slug='available',
            defaults={'name': 'Available', 'type': 'deployable', 'color': '28a745'}
        )
        in_use_status, _ = StatusLabel.objects.get_or_create(
            slug='in-use',
            defaults={'name': 'In Use', 'type': 'deployed', 'color': '007bff'}
        )

        role = AssetRole.objects.create(name='Test Role', slug='test-role')
        asset = Asset.objects.create(
            name='Test Checkout Laptop',
            asset_tag='TAG-CHK-TEST',
            status=available_status,
            asset_role=role
        )

        holder = AssetHolder.objects.create(first_name='John', last_name='Tester', upn='john.tester')

        # Set middleware variables
        _current_user.set(self.user)
        _request_id.set(uuid.uuid4())

        # Perform checkout
        checkout_asset(asset, holder=holder, user=self.user)

        # Refresh from db
        asset.refresh_from_db()
        self.assertEqual(asset.status, in_use_status)

        # Check changelog entry has difference
        change = ObjectChange.objects.filter(changed_object_id=asset.pk, action='checkout').latest('time')
        self.assertEqual(change.prechange_data.get('status'), available_status.pk)
        self.assertEqual(change.postchange_data.get('status'), in_use_status.pk)
        self.assertNotEqual(change.prechange_data, change.postchange_data)

        _request_id.set(None)
        _current_user.set(None)

    def test_objectchange_tracking(self):
        # Set context variables to trigger change logging
        _current_user.set(self.user)
        req_id = uuid.uuid4()
        _request_id.set(req_id)

        site = Site.objects.create(name='Test Site', slug='test-site')
        # Action: Create
        channel = Location.objects.create(name='Test Channel', slug='test-channel', site=site)
        change_create = ObjectChange.objects.filter(request_id=req_id, action='create').first()
        self.assertIsNotNone(change_create)
        self.assertEqual(change_create.action, 'create')
        self.assertTrue(change_create.object_type_repr)
        self.assertIn("Created by", str(change_create))
        self.assertTrue(isinstance(change_create.get_absolute_url(), str))
        self.assertTrue(isinstance(change_create.get_changed_object_url(), str) or change_create.get_changed_object_url() is None)

        # Action: Update (and clean/snapshot coverage)
        channel.clean()
        channel.snapshot()
        channel.name = 'Updated Channel'
        channel.save()
        change_update = ObjectChange.objects.filter(request_id=req_id, action='update').first()
        self.assertIsNotNone(change_update)

        # Save without changes
        channel.save()

        # Action: Delete
        channel.delete()
        change_delete = ObjectChange.objects.filter(request_id=req_id, action='delete').first()
        self.assertIsNotNone(change_delete)

        # Cleanup context vars
        _request_id.set(None)
        _current_user.set(None)
