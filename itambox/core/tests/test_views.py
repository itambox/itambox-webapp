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

    def test_objectchange_hides_system_events_by_default(self):
        """System (core noise) events are hidden by default but shown when toggled."""
        from django.contrib.contenttypes.models import ContentType
        from core.models import Job
        from core.filters import ObjectChangeFilterSet

        request = self.factory.get('/')
        request.user = self.user
        middleware = CurrentUserMiddleware(get_response=lambda r: None)
        middleware.process_request(request)

        Job.objects.create(name="noisy-job-unique")
        Manufacturer.objects.create(name="RealChange unique-x", slug="realchange-unique-x")

        middleware.process_response(request, None)

        job_ct = ContentType.objects.get_for_model(Job)
        mfr_ct = ContentType.objects.get_for_model(Manufacturer)

        # Default: Job (system event) excluded, Manufacturer (real change) retained.
        default_qs = ObjectChangeFilterSet({}, queryset=ObjectChange.objects.all()).qs
        self.assertFalse(default_qs.filter(changed_object_type=job_ct).exists())
        self.assertTrue(default_qs.filter(changed_object_type=mfr_ct).exists())

        # Toggled on: Job changes become visible again.
        shown_qs = ObjectChangeFilterSet(
            {'show_system_events': 'on'}, queryset=ObjectChange.objects.all()
        ).qs
        self.assertTrue(shown_qs.filter(changed_object_type=job_ct).exists())

    def test_softdelete_management_views(self):
        """Test frontend soft-delete Recycle Bin, Restore, Purge, and Bulk actions."""
        self.client.force_login(self.user)
        
        # 1. Setup sample objects
        mfr = Manufacturer.objects.create(name="SoftDelete Manufacturer", slug="sd-mfr")
        role = AssetRole.objects.create(name="Laptop", slug="laptop")
        asset_type = AssetType.objects.create(manufacturer=mfr, model="ThinkPad", slug="thinkpad")
        
        asset = Asset.objects.create(
            name="Alice ThinkPad",
            asset_tag="TP-001",
            asset_type=asset_type,
            asset_role=role
        )
        
        # Verify active asset shows up in active list view but not in Recycle Bin
        active_list_url = reverse('assets:asset_list')
        response = self.client.get(active_list_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alice ThinkPad")
        
        recycle_bin_url = active_list_url + "?deleted=true"
        response = self.client.get(recycle_bin_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Alice ThinkPad")
        self.assertContains(response, "Recycle Bin — Assets")
        
        # Soft-delete the asset
        asset.delete()
        self.assertIsNotNone(Asset.all_objects.get(pk=asset.pk).deleted_at)
        self.assertFalse(Asset.objects.filter(pk=asset.pk).exists())
        
        # Verify it shows up in Recycle Bin but not active list
        response = self.client.get(active_list_url)
        self.assertNotContains(response, "Alice ThinkPad")
        
        response = self.client.get(recycle_bin_url)
        self.assertContains(response, "Alice ThinkPad")
        
        # 2. Test Single Restore View
        from django.contrib.contenttypes.models import ContentType
        ct = ContentType.objects.get_for_model(Asset)
        restore_url = reverse('object_restore', kwargs={'content_type_id': ct.pk, 'object_id': asset.pk})
        
        response = self.client.post(restore_url, follow=True)
        self.assertEqual(response.status_code, 200)
        
        # Verify asset is restored (deleted_at is None)
        asset.refresh_from_db()
        self.assertIsNone(asset.deleted_at)
        self.assertTrue(Asset.objects.filter(pk=asset.pk).exists())
        
        # Soft-delete again to test purge
        asset.delete()
        
        # 3. Test Single Purge View
        purge_url = reverse('object_purge', kwargs={'content_type_id': ct.pk, 'object_id': asset.pk})
        response = self.client.post(purge_url, follow=True)
        self.assertEqual(response.status_code, 200)
        
        # Verify asset is physically gone
        self.assertFalse(Asset.all_objects.filter(pk=asset.pk).exists())
        
        # 4. Test Bulk Restore / Purge Views
        asset2 = Asset.objects.create(
            name="Bob ThinkPad",
            asset_tag="TP-002",
            asset_type=asset_type,
            asset_role=role
        )
        asset3 = Asset.objects.create(
            name="Charlie ThinkPad",
            asset_tag="TP-003",
            asset_type=asset_type,
            asset_role=role
        )
        
        # Soft-delete both
        asset2.delete()
        asset3.delete()
        
        bulk_restore_url = reverse('object_bulk_restore', kwargs={'content_type_id': ct.pk})
        response = self.client.post(bulk_restore_url, {'pk': [asset2.pk, asset3.pk]}, follow=True)
        self.assertEqual(response.status_code, 200)
        
        # Verify both are restored
        asset2.refresh_from_db()
        asset3.refresh_from_db()
        self.assertIsNone(asset2.deleted_at)
        self.assertIsNone(asset3.deleted_at)
        
        # Soft-delete both again
        asset2.delete()
        asset3.delete()
        
        # Bulk Purge
        bulk_purge_url = reverse('object_bulk_purge', kwargs={'content_type_id': ct.pk})
        response = self.client.post(bulk_purge_url, {'pk': [asset2.pk, asset3.pk]}, follow=True)
        self.assertEqual(response.status_code, 200)
        
        # Verify both are completely purged
        self.assertFalse(Asset.all_objects.filter(pk__in=[asset2.pk, asset3.pk]).exists())

    def test_recycle_bin_permissions(self):
        """Test Recycle Bin access control for superusers, standard roles, and restricted users."""
        from organization.models import Tenant, TenantRole, TenantMembership
        from django.contrib.contenttypes.models import ContentType
        
        # 1. Setup tenant and standard user
        tenant = Tenant.objects.create(name="ACME Corp", slug="acme")
        std_user = User.objects.create_user(username='stduser', password='password123')
        
        # Setup asset
        mfr = Manufacturer.objects.create(name="Perm Mfr", slug="perm-mfr")
        role = AssetRole.objects.create(name="Laptop", slug="laptop")
        asset_type = AssetType.objects.create(manufacturer=mfr, model="ThinkPad", slug="thinkpad")
        
        asset = Asset.objects.create(
            name="Bob ThinkPad",
            asset_tag="TP-999",
            asset_type=asset_type,
            asset_role=role,
            tenant=tenant
        )
        asset.delete()
        
        # 2. Try to view Recycle Bin without permissions
        self.client.force_login(std_user)
        active_list_url = reverse('assets:asset_list')
        recycle_bin_url = active_list_url + "?deleted=true"
        
        response = self.client.get(recycle_bin_url)
        self.assertEqual(response.status_code, 403) # Forbidden
        
        # Try to restore without permissions
        ct = ContentType.objects.get_for_model(Asset)
        restore_url = reverse('object_restore', kwargs={'content_type_id': ct.pk, 'object_id': asset.pk})
        response = self.client.post(restore_url)
        self.assertEqual(response.status_code, 403) # Forbidden
        
        # Try to purge without permissions
        purge_url = reverse('object_purge', kwargs={'content_type_id': ct.pk, 'object_id': asset.pk})
        response = self.client.post(purge_url)
        self.assertEqual(response.status_code, 403) # Forbidden
        
        # 3. Create TenantRole with Recycle Bin permissions
        # We need to give view_asset/change_asset/delete_asset permissions as well for the standard asset views
        role_obj = TenantRole.objects.create(
            tenant=tenant,
            name="Recycle Bin Manager",
            permissions=[
                'assets.view_asset',
                'assets.change_asset',
                'assets.delete_asset',
                'core.view_recyclebin',
                'core.change_recyclebin',
                'core.delete_recyclebin',
            ]
        )
        TenantMembership.objects.create(user=std_user, tenant=tenant, role=role_obj)
        
        # 4. Try again with permissions
        # Force a reload of the user object to update cached memberships
        std_user = User.objects.get(pk=std_user.pk)
        
        # Set active tenant in session or request context if needed (scoping middleware handles it based on membership)
        response = self.client.get(recycle_bin_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bob ThinkPad")
        
        # Test restore
        response = self.client.post(restore_url, follow=True)
        self.assertEqual(response.status_code, 200)
        asset.refresh_from_db()
        self.assertIsNone(asset.deleted_at)
        
        # Soft-delete again for purge test
        asset.delete()
        
        # Test purge
        response = self.client.post(purge_url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Asset.all_objects.filter(pk=asset.pk).exists())

    def test_base_template_context_processor(self):
        """Test that the base_template_processor context processor correctly returns the expected base templates."""
        from django_htmx.middleware import HtmxDetails
        from itambox.context_processors import base_template_processor

        # 1. Normal Request (Non-HTMX)
        request = self.factory.get('/')
        context = base_template_processor(request)
        self.assertEqual(context['base_template'], 'layout.html')

        # 2. HTMX request but not boosted main swap
        request.htmx = HtmxDetails(request)
        context = base_template_processor(request)
        self.assertEqual(context['base_template'], 'layout.html')

        # 3. Boosted HTMX Request
        boosted_request = self.factory.get('/', HTTP_HX_REQUEST='true', HTTP_HX_BOOSTED='true')
        boosted_request.htmx = HtmxDetails(boosted_request)
        context = base_template_processor(boosted_request)
        self.assertEqual(context['base_template'], 'base_htmx.html')

        # 4. History Restore HTMX Request
        restore_request = self.factory.get('/', HTTP_HX_REQUEST='true', HTTP_HX_HISTORY_RESTORE_REQUEST='true')
        restore_request.htmx = HtmxDetails(restore_request)
        context = base_template_processor(restore_request)
        self.assertEqual(context['base_template'], 'base_htmx.html')

        # 5. Targeted swap HTMX Request
        targeted_request = self.factory.get('/', HTTP_HX_REQUEST='true', HTTP_HX_TARGET='page-content-wrapper')
        targeted_request.htmx = HtmxDetails(targeted_request)
        context = base_template_processor(targeted_request)
        self.assertEqual(context['base_template'], 'base_htmx.html')

        # 6. Override base template using request attribute
        request.base_template = 'custom_base.html'
        context = base_template_processor(request)
        self.assertEqual(context['base_template'], 'custom_base.html')


