from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from organization.models import Tenant, TenantGroup, AssetHolder, TenantRole

User = get_user_model()

class TenantViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='password123', is_superuser=True, is_staff=True
        )
        self.client.force_login(self.user)
        self.tenant = Tenant.objects.create(name='Acme Corp', slug='acme-corp')

    def test_list_view(self):
        url = reverse('organization:tenant_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_detail_view(self):
        url = reverse('organization:tenant_detail', kwargs={'pk': self.tenant.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse('organization:tenant_create')
        response = self.client.post(url, {'name': 'Globex Inc', 'slug': 'globex-inc'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Tenant.objects.filter(name='Globex Inc').exists())

    def test_edit_view_post(self):
        url = reverse('organization:tenant_update', kwargs={'pk': self.tenant.pk})
        response = self.client.post(url, {
            'name': 'Acme Corp Renamed', 'slug': 'acme-corp-renamed'
        })
        if response.status_code != 302:
            form = response.context.get('form')
            if form:
                self.fail(f'Form invalid. Errors: {form.errors}')
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.name, 'Acme Corp Renamed')

    def test_delete_view_post(self):
        url = reverse('organization:tenant_delete', kwargs={'pk': self.tenant.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Tenant.objects.filter(pk=self.tenant.pk).exists())

class TenantGroupViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='password123', is_superuser=True, is_staff=True
        )
        self.client.force_login(self.user)
        self.group = TenantGroup.objects.create(name='Customers', slug='customers')

    def test_list_view(self):
        url = reverse('organization:tenantgroup_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_detail_view(self):
        url = reverse('organization:tenantgroup_detail', kwargs={'pk': self.group.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse('organization:tenantgroup_create')
        response = self.client.post(url, {'name': 'Vendors', 'slug': 'vendors'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(TenantGroup.objects.filter(name='Vendors').exists())

    def test_delete_view_post(self):
        url = reverse('organization:tenantgroup_delete', kwargs={'pk': self.group.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TenantGroup.objects.filter(pk=self.group.pk).exists())

class TenantGroupViewExpansionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='tenantadmin', password='testpassword', is_staff=True, is_superuser=True)
        self.client.force_login(self.user)
        self.group = TenantGroup.objects.create(name='Premium Clients', slug='premium-clients')

    def test_update_view_post(self):
        url = reverse('organization:tenantgroup_update', kwargs={'pk': self.group.pk})
        response = self.client.post(url, {'name': 'Premium Clients Updated', 'slug': 'premium-clients-updated'})
        self.assertEqual(response.status_code, 302)
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, 'Premium Clients Updated')

class MultiTenantMembershipAndInvitationTests(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        self.user = User.objects.create_user(
            username='staffuser', email='staff@example.com', password='password123'
        )
        # Create standard tenant roles
        self.role_admin = TenantRole.objects.create(
            tenant=self.tenant_a,
            name='Administrator',
            permissions=[
                'assets.view_asset', 'assets.add_asset', 'assets.change_asset', 'assets.delete_asset',
                'inventory.view_accessory', 'inventory.add_accessory', 'inventory.change_accessory', 'inventory.delete_accessory',
                'inventory.view_consumable', 'inventory.add_consumable', 'inventory.change_consumable', 'inventory.delete_consumable',
                'inventory.view_kit', 'inventory.add_kit', 'inventory.change_kit', 'inventory.delete_kit',
                'components.view_component', 'components.add_component', 'components.change_component', 'components.delete_component',
                'organization.view_location', 'organization.add_location', 'organization.change_location', 'organization.delete_location',
                'organization.view_site', 'organization.add_site', 'organization.change_site', 'organization.delete_site',
                'organization.view_assetholder', 'organization.add_assetholder', 'organization.change_assetholder', 'organization.delete_assetholder',
                'extras.view_dashboard', 'extras.add_dashboard', 'extras.change_dashboard', 'extras.delete_dashboard',
            ]
        )
        self.role_member = TenantRole.objects.create(
            tenant=self.tenant_a,
            name='Standard Member',
            permissions=[
                'assets.view_asset', 'assets.add_asset', 'assets.change_asset',
                'inventory.view_accessory', 'inventory.add_accessory', 'inventory.change_accessory',
                'inventory.view_consumable', 'inventory.add_consumable', 'inventory.change_consumable',
                'inventory.view_kit', 'inventory.add_kit', 'inventory.change_kit',
                'components.view_component', 'components.add_component', 'components.change_component',
                'organization.view_location', 'organization.add_location', 'organization.change_location',
                'organization.view_site', 'organization.add_site', 'organization.change_site',
                'organization.view_assetholder', 'organization.add_assetholder', 'organization.change_assetholder',
                'extras.view_dashboard', 'extras.add_dashboard', 'extras.change_dashboard',
            ]
        )
        self.role_reader = TenantRole.objects.create(
            tenant=self.tenant_a,
            name='Read-Only Viewer',
            permissions=[
                'assets.view_asset',
                'inventory.view_accessory',
                'inventory.view_consumable',
                'inventory.view_kit',
                'components.view_component',
                'organization.view_location',
                'organization.view_site',
                'organization.view_assetholder',
                'extras.view_dashboard',
            ]
        )

    def test_tenant_membership_creation_and_string_representation(self):
        from organization.models import TenantMembership
        membership = TenantMembership.objects.create(
            user=self.user,
            tenant=self.tenant_a,
            role=self.role_member
        )
        self.assertEqual(str(membership), "staffuser is Standard Member at Tenant A")
        self.assertEqual(membership.role, self.role_member)

    def test_invitation_acceptance_and_assetholder_linking(self):
        from organization.models import TenantInvitation, TenantMembership
        from django.utils import timezone
        
        holder = AssetHolder.objects.create(
            first_name="Beate",
            last_name="Office",
            upn="beate.office",
            email="beate@example.com",
            tenant=self.tenant_a
        )
        self.assertIsNone(holder.user)
        
        invitation = TenantInvitation.objects.create(
            email="beate@example.com",
            tenant=self.tenant_a,
            role=self.role_admin,
            expires_at=timezone.now() + timezone.timedelta(days=7)
        )
        self.assertTrue(invitation.is_valid)
        self.assertEqual(str(invitation), "Invite for beate@example.com to Tenant A")
        
        invitee_user = User.objects.create_user(
            username='beate_user', email='beate@example.com', password='password123'
        )
        from organization.models import accept_invitation
        accept_invitation(invitation, invitee_user)
        
        membership = TenantMembership.objects.get(user=invitee_user, tenant=self.tenant_a)
        self.assertEqual(membership.role, self.role_admin)
        
        invitation.refresh_from_db()
        self.assertFalse(invitation.is_valid)
        self.assertIsNotNone(invitation.accepted_at)
        
        holder.refresh_from_db()
        self.assertEqual(holder.user, invitee_user)

    def test_tenant_membership_backend_permissions(self):
        from organization.models import TenantMembership
        from core.managers import set_current_membership
        
        reader_mem = TenantMembership.objects.create(
            user=self.user,
            tenant=self.tenant_a,
            role=self.role_reader
        )
        
        set_current_membership(reader_mem)
        self.assertTrue(self.user.has_perm('assets.view_asset'))
        self.assertFalse(self.user.has_perm('assets.add_asset'))
        
        reader_mem.role = self.role_member
        reader_mem.save()
        set_current_membership(reader_mem)
        self.assertTrue(self.user.has_perm('assets.view_asset'))
        self.assertTrue(self.user.has_perm('assets.add_asset'))
        self.assertFalse(self.user.has_perm('assets.delete_asset'))
        
        reader_mem.role = self.role_admin
        reader_mem.save()
        set_current_membership(reader_mem)
        self.assertTrue(self.user.has_perm('assets.view_asset'))
        self.assertTrue(self.user.has_perm('assets.add_asset'))
        self.assertTrue(self.user.has_perm('assets.delete_asset'))
        
        set_current_membership(None)

    def test_tenant_switching_middleware(self):
        from organization.models import TenantMembership
        from django.contrib.sessions.middleware import SessionMiddleware
        from itambox.middleware import TenantMiddleware
        from django.test import RequestFactory
        
        TenantMembership.objects.create(
            user=self.user,
            tenant=self.tenant_a,
            role=self.role_member
        )
        
        factory = RequestFactory()
        
        request = factory.get('/')
        request.user = self.user
        
        middleware = SessionMiddleware(lambda r: None)
        middleware.process_request(request)
        
        tenant_middleware = TenantMiddleware(lambda r: None)
        tenant_middleware.process_request(request)
        
        self.assertEqual(request.active_tenant, self.tenant_a)
        self.assertEqual(request.session['active_tenant_id'], self.tenant_a.id)
        
        request = factory.get('/?switch_tenant={}'.format(self.tenant_b.id))
        request.user = self.user
        middleware.process_request(request)
        tenant_middleware.process_request(request)
        
        self.assertEqual(request.active_tenant, self.tenant_a)
