from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from core.middleware import CurrentUserMiddleware, get_current_user, get_current_request_id
from core.utils import serialize_object
from core.models import ObjectChange
from assets.models import Manufacturer, AssetRole, Asset
from licenses.models import License, LicenseSeatAssignment
from software.models import Software
from users.views import UserPreferencesView

User = get_user_model()

class CoreRefactoringTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username='testuser', password='password123')
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

