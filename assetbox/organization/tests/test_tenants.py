from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from organization.models import Tenant, TenantGroup, AssetHolder

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

    def test_tenant_membership_creation_and_string_representation(self):
        from organization.models import TenantMembership, TenantRole
        membership = TenantMembership.objects.create(
            user=self.user,
            tenant=self.tenant_a,
            role=TenantRole.MEMBER
        )
        self.assertEqual(str(membership), "staffuser is Standard Member at Tenant A")
        self.assertEqual(membership.role, TenantRole.MEMBER)

    def test_invitation_acceptance_and_assetholder_linking(self):
        from organization.models import TenantInvitation, TenantRole, TenantMembership
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
            role=TenantRole.ADMIN,
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
        self.assertEqual(membership.role, TenantRole.ADMIN)
        
        invitation.refresh_from_db()
        self.assertFalse(invitation.is_valid)
        self.assertIsNotNone(invitation.accepted_at)
        
        holder.refresh_from_db()
        self.assertEqual(holder.user, invitee_user)

    def test_tenant_membership_backend_permissions(self):
        from organization.models import TenantMembership, TenantRole
        from core.managers import set_current_membership
        
        reader_mem = TenantMembership.objects.create(
            user=self.user,
            tenant=self.tenant_a,
            role=TenantRole.READER
        )
        
        set_current_membership(reader_mem)
        self.assertTrue(self.user.has_perm('assets.view_asset'))
        self.assertFalse(self.user.has_perm('assets.add_asset'))
        
        reader_mem.role = TenantRole.MEMBER
        reader_mem.save()
        set_current_membership(reader_mem)
        self.assertTrue(self.user.has_perm('assets.view_asset'))
        self.assertTrue(self.user.has_perm('assets.add_asset'))
        self.assertFalse(self.user.has_perm('assets.delete_asset'))
        
        reader_mem.role = TenantRole.ADMIN
        reader_mem.save()
        set_current_membership(reader_mem)
        self.assertTrue(self.user.has_perm('assets.view_asset'))
        self.assertTrue(self.user.has_perm('assets.add_asset'))
        self.assertTrue(self.user.has_perm('assets.delete_asset'))
        
        set_current_membership(None)

    def test_tenant_switching_middleware(self):
        from organization.models import TenantMembership, TenantRole
        from django.contrib.sessions.middleware import SessionMiddleware
        from assetbox.middleware import TenantMiddleware
        from django.test import RequestFactory
        
        TenantMembership.objects.create(
            user=self.user,
            tenant=self.tenant_a,
            role=TenantRole.MEMBER
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
