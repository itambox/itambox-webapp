from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.contrib.contenttypes.models import ContentType
from .models import (
    Contact, ContactRole, ContactAssignment, Region, Site, SiteGroup,
    Tenant, TenantGroup, Location, AssetHolder,
)
from assets.models import Manufacturer

User = get_user_model()

class ContactsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testadmin', password='password123', is_superuser=True, is_staff=True)
        self.client.force_login(self.user)
        
        # Create standard Manufacturer
        self.manufacturer = Manufacturer.objects.create(name='Dell Technologies', slug='dell')
        
        # Create Contact Roles
        self.support_role = ContactRole.objects.create(name='Technical Support')
        self.sales_role = ContactRole.objects.create(name='Sales Rep')
        
        # Create Contact
        self.contact = Contact.objects.create(
            name='Dell Enterprise Support',
            email='enterprise-support@dell.com',
            phone='+1-800-456-3355',
            web_url='https://support.dell.com'
        )

    def test_contact_role_slug_auto_generation(self):
        """Test that ContactRole automatically generates slug if not provided."""
        role = ContactRole.objects.create(name='Billing Department')
        self.assertEqual(role.slug, 'billing-department')

    def test_contact_assignment_and_manufacturer_resolver(self):
        """Test that Manufacturer resolves support contact correctly through get_support_contact."""
        mfr_ct = ContentType.objects.get_for_model(self.manufacturer)
        
        # 1. No contact assigned
        self.assertIsNone(self.manufacturer.get_support_contact)
        
        # 2. Assign contact with support role
        ContactAssignment.objects.create(
            contact=self.contact,
            role=self.support_role,
            content_type=mfr_ct,
            object_id=self.manufacturer.pk,
            priority='primary'
        )
        
        self.assertEqual(self.manufacturer.get_support_contact, self.contact)

    def test_contacts_crud_views(self):
        """Test that Contacts CRUD views resolve correctly and handle submissions."""
        # 1. List
        list_url = reverse('organization:contact_list')
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)

        # 2. Create
        create_url = reverse('organization:contact_create')
        response = self.client.get(create_url)
        self.assertEqual(response.status_code, 200)
        
        post_data = {
            'name': 'Lenovo Support',
            'email': 'support@lenovo.com',
        }
        response = self.client.post(create_url, post_data)
        self.assertEqual(response.status_code, 302) # Redirects to absolute url (detail page)
        self.assertTrue(Contact.objects.filter(name='Lenovo Support').exists())

        # 3. Detail
        detail_url = reverse('organization:contact_detail', kwargs={'pk': self.contact.pk})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)

        # 4. Edit
        edit_url = reverse('organization:contact_update', kwargs={'pk': self.contact.pk})
        response = self.client.get(edit_url)
        self.assertEqual(response.status_code, 200)

        # 5. Delete
        delete_url = reverse('organization:contact_delete', kwargs={'pk': self.contact.pk})
        response = self.client.get(delete_url)
        self.assertEqual(response.status_code, 200)

    def test_contact_roles_crud_views(self):
        """Test that ContactRoles CRUD views resolve correctly."""
        # 1. List
        list_url = reverse('organization:contactrole_list')
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)

        # 2. Create
        create_url = reverse('organization:contactrole_create')
        response = self.client.get(create_url)
        self.assertEqual(response.status_code, 200)

        # 3. Detail
        detail_url = reverse('organization:contactrole_detail', kwargs={'pk': self.support_role.pk})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)

    def test_contact_assignment_views(self):
        """Test ContactAssignmentCreateView and ContactAssignmentDeleteView."""
        mfr_ct = ContentType.objects.get_for_model(self.manufacturer)
        
        # 1. GET ContactAssignmentCreateView
        assign_url = reverse('organization:contactassignment_create')
        response = self.client.get(assign_url, {'content_type': mfr_ct.id, 'object_id': self.manufacturer.pk})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Assign Contact', response.content)
        
        # 2. POST ContactAssignmentCreateView
        post_data = {
            'contact': self.contact.pk,
            'role': self.support_role.pk,
            'priority': 'primary',
            'content_type': mfr_ct.id,
            'object_id': self.manufacturer.pk,
        }
        response = self.client.post(assign_url, post_data)
        self.assertEqual(response.status_code, 302) # Redirects back to Manufacturer detail view
        
        assignment = ContactAssignment.objects.get(contact=self.contact, role=self.support_role)
        self.assertEqual(assignment.priority, 'primary')
        
        # 3. DELETE ContactAssignmentDeleteView
        delete_url = reverse('organization:contactassignment_delete', kwargs={'pk': assignment.pk})
        response = self.client.post(delete_url, {'confirm': 'yes'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ContactAssignment.objects.filter(pk=assignment.pk).exists())


class RegionViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='password123', is_superuser=True, is_staff=True
        )
        self.client.force_login(self.user)
        self.region = Region.objects.create(name='North America', slug='north-america')

    def test_list_view(self):
        url = reverse('organization:region_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_detail_view(self):
        url = reverse('organization:region_detail', kwargs={'pk': self.region.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse('organization:region_create')
        response = self.client.post(url, {'name': 'Europe', 'slug': 'europe'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Region.objects.filter(name='Europe').exists())

    def test_edit_view_post(self):
        url = reverse('organization:region_update', kwargs={'pk': self.region.pk})
        response = self.client.post(url, {
            'name': 'North America Updated', 'slug': 'north-america-updated'
        })
        self.assertEqual(response.status_code, 302)
        self.region.refresh_from_db()
        self.assertEqual(self.region.name, 'North America Updated')

    def test_delete_view_post(self):
        url = reverse('organization:region_delete', kwargs={'pk': self.region.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Region.objects.filter(pk=self.region.pk).exists())


class SiteViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='password123', is_superuser=True, is_staff=True
        )
        self.client.force_login(self.user)
        self.site = Site.objects.create(name='HQ Office', slug='hq-office', status='active')

    def test_list_view(self):
        url = reverse('organization:site_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_detail_view(self):
        url = reverse('organization:site_detail', kwargs={'pk': self.site.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse('organization:site_create')
        response = self.client.post(url, {
            'name': 'Branch Office', 'slug': 'branch-office', 'status': 'active'
        })
        if response.status_code != 302:
            form = response.context.get('form')
            if form:
                self.fail(f'Form invalid. Errors: {form.errors}')
        self.assertTrue(Site.objects.filter(name='Branch Office').exists())

    def test_edit_view_post(self):
        url = reverse('organization:site_update', kwargs={'pk': self.site.pk})
        response = self.client.post(url, {
            'name': 'HQ Office Renamed', 'slug': 'hq-office-renamed', 'status': 'active'
        })
        if response.status_code != 302:
            form = response.context.get('form')
            if form:
                self.fail(f'Form invalid. Errors: {form.errors}')
        self.site.refresh_from_db()
        self.assertEqual(self.site.name, 'HQ Office Renamed')

    def test_delete_view_post(self):
        url = reverse('organization:site_delete', kwargs={'pk': self.site.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Site.objects.filter(pk=self.site.pk).exists())


class LocationViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='password123', is_superuser=True, is_staff=True
        )
        self.client.force_login(self.user)
        self.site = Site.objects.create(name='Test Site', slug='test-site', status='active')
        self.location = Location.objects.create(name='Server Room', slug='server-room', site=self.site)

    def test_list_view(self):
        url = reverse('organization:location_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_detail_view(self):
        url = reverse('organization:location_detail', kwargs={'pk': self.location.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse('organization:location_create')
        response = self.client.post(url, {
            'name': 'Network Closet', 'slug': 'network-closet', 'site': self.site.pk, 'status': 'active'
        })
        if response.status_code != 302:
            form = response.context.get('form')
            if form:
                self.fail(f'Form invalid. Errors: {form.errors}')
        self.assertTrue(Location.objects.filter(name='Network Closet').exists())

    def test_edit_view_post(self):
        url = reverse('organization:location_update', kwargs={'pk': self.location.pk})
        response = self.client.post(url, {
            'name': 'Server Room 2', 'slug': 'server-room-2', 'site': self.site.pk, 'status': 'active'
        })
        if response.status_code != 302:
            form = response.context.get('form')
            if form:
                self.fail(f'Form invalid. Errors: {form.errors}')
        self.location.refresh_from_db()
        self.assertEqual(self.location.name, 'Server Room 2')

    def test_delete_view_post(self):
        url = reverse('organization:location_delete', kwargs={'pk': self.location.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Location.objects.filter(pk=self.location.pk).exists())


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


class AssetHolderViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='password123', is_superuser=True, is_staff=True
        )
        self.client.force_login(self.user)
        self.holder = AssetHolder.objects.create(
            first_name='Alice', last_name='Johnson', upn='alice.johnson', email='alice@test.com'
        )

    def test_list_view(self):
        url = reverse('organization:assetholder_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_detail_view(self):
        url = reverse('organization:assetholder_detail', kwargs={'pk': self.holder.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse('organization:assetholder_create')
        response = self.client.post(url, {
            'first_name': 'Bob',
            'last_name': 'Smith',
            'upn': 'bob.smith',
            'email': 'bob@test.com',
        })
        if response.status_code != 302:
            form = response.context.get('form')
            if form:
                self.fail(f'Form invalid. Errors: {form.errors}')
        self.assertTrue(AssetHolder.objects.filter(upn='bob.smith').exists())

    def test_edit_view_post(self):
        url = reverse('organization:assetholder_update', kwargs={'pk': self.holder.pk})
        response = self.client.post(url, {
            'first_name': 'Alice',
            'last_name': 'Johnson-Smith',
            'upn': 'alice.johnson',
            'email': 'alice@test.com',
        })
        if response.status_code != 302:
            form = response.context.get('form')
            if form:
                self.fail(f'Form invalid. Errors: {form.errors}')
        self.holder.refresh_from_db()
        self.assertEqual(self.holder.last_name, 'Johnson-Smith')

    def test_delete_view_post(self):
        url = reverse('organization:assetholder_delete', kwargs={'pk': self.holder.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AssetHolder.objects.filter(pk=self.holder.pk).exists())


class SiteGroupViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='password123', is_superuser=True, is_staff=True
        )
        self.client.force_login(self.user)
        self.group = SiteGroup.objects.create(name='Data Centers', slug='data-centers')

    def test_list_view(self):
        url = reverse('organization:sitegroup_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_detail_view(self):
        url = reverse('organization:sitegroup_detail', kwargs={'pk': self.group.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse('organization:sitegroup_create')
        response = self.client.post(url, {'name': 'Branch Offices', 'slug': 'branch-offices'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(SiteGroup.objects.filter(name='Branch Offices').exists())

    def test_delete_view_post(self):
        url = reverse('organization:sitegroup_delete', kwargs={'pk': self.group.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SiteGroup.objects.filter(pk=self.group.pk).exists())


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
