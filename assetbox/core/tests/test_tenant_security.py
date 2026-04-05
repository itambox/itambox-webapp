from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.urls import reverse
from organization.models import TenantGroup, Tenant, TenantMembership, TenantRole, Site, Location
from assets.models import StatusLabel, Asset, AssetRole, Manufacturer, AssetType

User = get_user_model()

class CoreTenantSecurityTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username='testuser', password='password')
        self.superuser = User.objects.create_superuser(username='admin', password='password')
        self.tenant_group = TenantGroup.objects.create(name='Global Group', slug='global-group')
        self.tenant = Tenant.objects.create(name='Test Tenant', slug='test-tenant', group=self.tenant_group)
        self.site = Site.objects.create(name='Test Site', slug='test-site')
        self.location = Location.objects.create(name='Test Location', slug='test-location', tenant=self.tenant, site=self.site)
        self.membership = TenantMembership.objects.create(user=self.user, tenant=self.tenant)

    def test_tenant_group_scoping(self):
        from core.managers import set_current_tenant_group
        from assetbox.middleware import _current_user
        
        # Superuser scoping
        _current_user.set(self.superuser)
        set_current_tenant_group(self.tenant_group)
        self.assertEqual(Tenant.objects.count(), 1)
        self.assertEqual(Location.objects.count(), 1)

        # Standard user scoping
        _current_user.set(self.user)
        self.assertEqual(Tenant.objects.count(), 1)
        self.assertEqual(Location.objects.count(), 1)

        # Anonymous scoping
        _current_user.set(None)
        self.assertEqual(Tenant.objects.count(), 1)
        self.assertEqual(Location.objects.count(), 1)

        # Cleanup
        set_current_tenant_group(None)

    def test_tenant_group_membership_isolation(self):
        """Test that a user cannot edit an asset of a tenant where they are reader, even if switched to an admin tenant."""
        # 1. Create TenantGroup and two tenants in the same group
        group = TenantGroup.objects.create(name='Test Group 2', slug='test-group-2')
        tenant_admin = Tenant.objects.create(name='Admin Tenant', slug='admin-tenant', group=group)
        tenant_readonly = Tenant.objects.create(name='Readonly Tenant', slug='readonly-tenant', group=group)
        
        # 2. Create status & role
        status = StatusLabel.objects.create(name='Test Active', slug='test-active', type='deployable')
        role = AssetRole.objects.create(name='Test Role', slug='test-role')
        
        mfr = Manufacturer.objects.create(name='Dell', slug='dell')
        asset_type = AssetType.objects.create(manufacturer=mfr, model='Latitude 5550')
        
        # 3. Create asset belonging to the readonly tenant
        asset_readonly = Asset.objects.create(
            name='Protected Desktop',
            asset_tag='TAG-PROT',
            status=status,
            asset_role=role,
            tenant=tenant_readonly
        )
        
        # Create a non-superuser user
        test_user = User.objects.create_user(username='tenant_test_user', password='password123', is_superuser=False)
        
        # 4. Bind memberships
        TenantMembership.objects.create(user=test_user, tenant=tenant_admin, role=TenantRole.ADMIN)
        TenantMembership.objects.create(user=test_user, tenant=tenant_readonly, role=TenantRole.READER)
        
        # Set active context in test client session
        self.client.force_login(test_user)
        session = self.client.session
        session['active_tenant_id'] = tenant_admin.pk
        session.save()
        
        # 5. Set active context to the ADMIN tenant
        from core.managers import set_current_tenant, set_current_membership
        membership_admin = TenantMembership.objects.get(user=test_user, tenant=tenant_admin)
        set_current_tenant(tenant_admin)
        set_current_membership(membership_admin)
        
        # 6. Verify that the user has general 'change_asset' permission (under active context)
        self.assertTrue(test_user.has_perm('assets.change_asset'))
        
        # 7. BUT verify that the user CANNOT edit the specific asset of the READONLY tenant!
        self.assertFalse(test_user.has_perm('assets.change_asset', obj=asset_readonly))
        
        # 8. Test that GET/POST requests are blocked (scoped out, resulting in 404 Not Found) for the readonly tenant asset
        
        # Update GET
        url_update = reverse('assets:asset_update', kwargs={'pk': asset_readonly.pk})
        response = self.client.get(url_update)
        self.assertEqual(response.status_code, 404)
        
        # Delete GET
        url_delete = reverse('assets:asset_delete', kwargs={'pk': asset_readonly.pk})
        response = self.client.get(url_delete)
        self.assertEqual(response.status_code, 404)
        
        # Clone GET
        url_clone = reverse('assets:asset_clone', kwargs={'pk': asset_readonly.pk})
        response = self.client.get(url_clone)
        self.assertEqual(response.status_code, 404)
        
        # Checkout GET (modal)
        url_checkout = reverse('assets:asset_checkout_modal', kwargs={'pk': asset_readonly.pk})
        response = self.client.get(url_checkout)
        self.assertEqual(response.status_code, 404)
        
        # Checkin POST
        url_checkin = reverse('assets:asset_checkin', kwargs={'pk': asset_readonly.pk})
        response = self.client.post(url_checkin)
        self.assertEqual(response.status_code, 404)
        
        # 9. Test that creating an asset and assigning it to the readonly tenant is blocked by form validation
        url_create = reverse('assets:asset_create')
        post_data = {
            'name': 'Illegally Assigned Laptop',
            'asset_tag': 'TAG-ILLEGAL',
            'status': status.pk,
            'asset_type': asset_type.pk,
            'asset_role': role.pk,
            'tenant': tenant_readonly.pk,
        }
        response = self.client.post(url_create, data=post_data)
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertIn('tenant', form.errors)
        self.assertEqual(form.errors['tenant'][0], "Select a valid choice. That choice is not one of the available choices.")
        
        # Cleanup context
        set_current_tenant(None)
        set_current_membership(None)
