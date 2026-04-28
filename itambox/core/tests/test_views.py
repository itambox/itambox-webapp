from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.urls import reverse
from users.views import UserPreferencesView
from core.models import ObjectChange
from assets.models import Manufacturer, AssetRole, AssetType, Asset
from itambox.middleware import CurrentUserMiddleware

User = get_user_model()

class CoreViewsTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username='testuser', password='password123', is_superuser=True)

    def test_user_preferences_view_mro_inheritance(self):
        """Test that UserPreferencesView has BaseHTMXView in MRO before TemplateResponseMixin."""
        mro = UserPreferencesView.__mro__
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
        self.assertIn(b'hx-swap-oob="true"', response.content)
        self.assertIn(b'id="page-title-block"', response.content)
        self.assertIn(b'id="breadcrumbs-block"', response.content)
        
        # 3. HTMX History Restore GET request
        history_headers = {
            'HTTP_HX_Request': 'true',
            'HTTP_HX_History_Restore_Request': 'true',
        }
        response = self.client.get(url, **history_headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context.get('base_template'), 'base_htmx.html')
        self.assertIn(b'<form', response.content)
        self.assertIn(b'hx-swap-oob="true"', response.content)

    def test_objectchange_resolved_data(self):
        """Test that the ObjectChange detail view successfully resolves primary keys to string representations."""
        self.client.force_login(self.user)
        
        request = self.factory.get('/')
        request.user = self.user
        middleware = CurrentUserMiddleware(get_response=lambda r: None)
        middleware.process_request(request)
        
        mfr = Manufacturer.objects.create(name="Microsoft unique-test-change", slug="microsoft-unique-test-change")
        role = AssetRole.objects.create(name="Laptop unique-test-change", slug="laptop-unique-test-change")
        asset_type = AssetType.objects.create(manufacturer=mfr, model="Surface Book unique-test-change", slug="surface-book-unique-test-change")
        
        asset = Asset.objects.create(
            name="Alice Surface",
            asset_tag="SRF-001",
            asset_type=asset_type,
            asset_role=role
        )
        
        middleware.process_response(request, None)
        
        change = ObjectChange.objects.filter(changed_object_id=asset.pk).latest('time')
        
        response = self.client.get(reverse('objectchange', args=[change.pk]))
        self.assertEqual(response.status_code, 200)
        
        self.assertContains(response, "Microsoft unique-test-change")
        self.assertContains(response, "Laptop unique-test-change")
        self.assertContains(response, "Surface Book unique-test-change")

    def test_objectchange_filtering(self):
        """Test that the ObjectChange list view can be searched and filtered by action, name, etc."""
        self.client.force_login(self.user)
        
        request = self.factory.get('/')
        request.user = self.user
        middleware = CurrentUserMiddleware(get_response=lambda r: None)
        middleware.process_request(request)
        
        mfr1 = Manufacturer.objects.create(name="Intel unique-filter-1", slug="intel-unique-filter-1")
        mfr2 = Manufacturer.objects.create(name="AMD unique-filter-2", slug="amd-unique-filter-2")
        
        middleware.process_response(request, None)
        
        # Verify base view renders without error
        response = self.client.get(reverse('objectchange_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Intel unique-filter-1")
        self.assertContains(response, "AMD unique-filter-2")
        
        # Verify search query filtering (q) for mfr1
        response = self.client.get(reverse('objectchange_list') + "?q=Intel")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Intel unique-filter-1")
        self.assertNotContains(response, "AMD unique-filter-2")
        
        # Verify search query filtering (q) for mfr2
        response = self.client.get(reverse('objectchange_list') + "?q=AMD")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AMD unique-filter-2")
        self.assertNotContains(response, "Intel unique-filter-1")
        
        # Verify action filtering
        response = self.client.get(reverse('objectchange_list') + "?action=create")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Intel unique-filter-1")
        
        # Verify filtering with multiple actions (both matching)
        response = self.client.get(reverse('objectchange_list') + "?action=create&action=update")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Intel unique-filter-1")
        self.assertContains(response, "AMD unique-filter-2")
        
        # Verify filtering with a non-matching action list
        response = self.client.get(reverse('objectchange_list') + "?action=update&action=delete")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Intel unique-filter-1")
        self.assertNotContains(response, "AMD unique-filter-2")
        
        # Verify filtering with multiple content types
        from django.contrib.contenttypes.models import ContentType
        ct_mfr = ContentType.objects.get_for_model(Manufacturer)
        response = self.client.get(reverse('objectchange_list') + f"?changed_object_type={ct_mfr.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Intel unique-filter-1")
