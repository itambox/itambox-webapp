from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.contrib.contenttypes.models import ContentType
from .models import (
    Contact, ContactRole, ContactAssignment, Region, Site, SiteGroup,
    Tenant, TenantGroup, Location, AssetHolder, AssetHolderAssignment,
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


class AssetHolderAssignmentFilterSetTests(TestCase):
    def setUp(self):
        from django.contrib.contenttypes.models import ContentType
        from organization.models import AssetHolder, AssetHolderAssignment
        from assets.models import Asset, StatusLabel
        
        self.holder1 = AssetHolder.objects.create(
            first_name='Alice', last_name='Smith', upn='alice.smith', email='alice@test.com'
        )
        self.holder2 = AssetHolder.objects.create(
            first_name='Bob', last_name='Jones', upn='bob.jones', email='bob@test.com'
        )
        
        self.status = StatusLabel.objects.get_or_create(
            slug="available", defaults={'name': 'Available', 'type': StatusLabel.TYPE_DEPLOYABLE}
        )[0]

        self.asset1 = Asset.objects.create(
            name="Laptop 1", asset_tag="TAG-1", serial_number="SN-1", status=self.status
        )
        self.asset2 = Asset.objects.create(
            name="Laptop 2", asset_tag="TAG-2", serial_number="SN-2", status=self.status
        )
        
        self.ct = ContentType.objects.get_for_model(Asset)
        
        self.assign1 = AssetHolderAssignment.objects.create(
            asset_holder=self.holder1, content_type=self.ct, object_id=self.asset1.pk
        )
        self.assign2 = AssetHolderAssignment.objects.create(
            asset_holder=self.holder2, content_type=self.ct, object_id=self.asset2.pk
        )

    def test_filter_by_asset_holder(self):
        from organization.filters import AssetHolderAssignmentFilterSet
        from organization.models import AssetHolderAssignment
        f = AssetHolderAssignmentFilterSet({'asset_holder': self.holder1.pk}, queryset=AssetHolderAssignment.objects.all())
        self.assertTrue(f.is_valid())
        self.assertIn(self.assign1, f.qs)
        self.assertNotIn(self.assign2, f.qs)

    def test_filter_by_content_type(self):
        from organization.filters import AssetHolderAssignmentFilterSet
        from organization.models import AssetHolderAssignment
        f = AssetHolderAssignmentFilterSet({'content_type': self.ct.pk}, queryset=AssetHolderAssignment.objects.all())
        self.assertTrue(f.is_valid())
        self.assertIn(self.assign1, f.qs)
        self.assertIn(self.assign2, f.qs)

    def test_filter_search(self):
        from organization.filters import AssetHolderAssignmentFilterSet
        from organization.models import AssetHolderAssignment
        f = AssetHolderAssignmentFilterSet({'q': 'alice'}, queryset=AssetHolderAssignment.objects.all())
        self.assertTrue(f.is_valid())
        self.assertIn(self.assign1, f.qs)
        self.assertNotIn(self.assign2, f.qs)


class ContactRoleViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='roleadmin', password='testpassword', is_staff=True, is_superuser=True)
        self.client.force_login(self.user)
        self.role = ContactRole.objects.create(name='Key Support', slug='key-support')

    def test_update_view_post(self):
        url = reverse('organization:contactrole_update', kwargs={'pk': self.role.pk})
        response = self.client.post(url, {'name': 'Key Support Updated', 'slug': 'key-support-updated'})
        self.assertEqual(response.status_code, 302)
        self.role.refresh_from_db()
        self.assertEqual(self.role.name, 'Key Support Updated')

    def test_delete_view_post(self):
        url = reverse('organization:contactrole_delete', kwargs={'pk': self.role.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ContactRole.objects.filter(pk=self.role.pk).exists())


class SiteGroupViewExpansionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='siteadmin', password='testpassword', is_staff=True, is_superuser=True)
        self.client.force_login(self.user)
        self.group = SiteGroup.objects.create(name='EU DCs', slug='eu-dcs')

    def test_update_view_post(self):
        url = reverse('organization:sitegroup_update', kwargs={'pk': self.group.pk})
        response = self.client.post(url, {'name': 'EU DCs Updated', 'slug': 'eu-dcs-updated'})
        self.assertEqual(response.status_code, 302)
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, 'EU DCs Updated')


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


class HierarchyValidationTests(TestCase):
    def setUp(self):
        self.region = Region.objects.create(name='Global', slug='global')
        self.site = Site.objects.create(name='Global HQ', slug='global-hq', status='active')
        self.location = Location.objects.create(name='Server Room', slug='server-room', site=self.site)
        self.site_group = SiteGroup.objects.create(name='Main HQ Sites', slug='main-hq-sites')
        self.tenant_group = TenantGroup.objects.create(name='Internal Entities', slug='internal-entities')

    def test_region_cannot_be_own_parent(self):
        from organization.forms.region_form import RegionForm
        form = RegionForm(instance=self.region, data={
            'name': 'Global',
            'slug': 'global',
            'parent': self.region.pk,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('parent', form.errors)
        self.assertEqual(form.errors['parent'][0], "A region cannot be its own parent.")

    def test_location_cannot_be_own_parent(self):
        from organization.forms.location_form import LocationForm
        form = LocationForm(instance=self.location, data={
            'name': 'Server Room',
            'slug': 'server-room',
            'site': self.site.pk,
            'status': 'active',
            'parent': self.location.pk,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('parent', form.errors)
        self.assertEqual(form.errors['parent'][0], "A location cannot be its own parent.")

    def test_site_group_cannot_be_own_parent(self):
        from organization.forms.sitegroup_form import SiteGroupForm
        form = SiteGroupForm(instance=self.site_group, data={
            'name': 'Main HQ Sites',
            'slug': 'main-hq-sites',
            'parent': self.site_group.pk,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('parent', form.errors)
        self.assertEqual(form.errors['parent'][0], "A site group cannot be its own parent.")

    def test_tenant_group_cannot_be_own_parent(self):
        from organization.forms.tenantgroup_form import TenantGroupForm
        form = TenantGroupForm(instance=self.tenant_group, data={
            'name': 'Internal Entities',
            'slug': 'internal-entities',
            'parent': self.tenant_group.pk,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('parent', form.errors)
        self.assertEqual(form.errors['parent'][0], "A tenant group cannot be its own parent.")


class OrganizationTenantScopingTests(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")

        # Sites
        self.site_a = Site.objects.create(name="Site A", slug="site-a", tenant=self.tenant_a)
        self.site_b = Site.objects.create(name="Site B", slug="site-b", tenant=self.tenant_b)
        self.site_global = Site.objects.create(name="Site Global", slug="site-global", tenant=None)

        # Locations (must belong to a Site)
        self.loc_a = Location.objects.create(name="Location A", slug="loc-a", site=self.site_a, tenant=self.tenant_a)
        self.loc_b = Location.objects.create(name="Location B", slug="loc-b", site=self.site_b, tenant=self.tenant_b)
        self.loc_global = Location.objects.create(name="Location Global", slug="loc-global", site=self.site_global, tenant=None)

        # AssetHolders
        self.holder_a = AssetHolder.objects.create(
            first_name="Holder", last_name="A", upn="holder.a", tenant=self.tenant_a
        )
        self.holder_b = AssetHolder.objects.create(
            first_name="Holder", last_name="B", upn="holder.b", tenant=self.tenant_b
        )
        self.holder_global = AssetHolder.objects.create(
            first_name="Holder", last_name="Global", upn="holder.global", tenant=None
        )

    def tearDown(self):
        from core.managers import set_current_tenant
        set_current_tenant(None)

    def test_tenant_a_scoping(self):
        from core.managers import set_current_tenant
        set_current_tenant(self.tenant_a)

        # Tenants
        tenants = list(Tenant.objects.all())
        self.assertIn(self.tenant_a, tenants)
        self.assertNotIn(self.tenant_b, tenants)

        # Sites
        sites = list(Site.objects.all())
        self.assertIn(self.site_a, sites)
        self.assertIn(self.site_global, sites)
        self.assertNotIn(self.site_b, sites)

        # Locations
        locs = list(Location.objects.all())
        self.assertIn(self.loc_a, locs)
        self.assertIn(self.loc_global, locs)
        self.assertNotIn(self.loc_b, locs)

        # AssetHolders
        holders = list(AssetHolder.objects.all())
        self.assertIn(self.holder_a, holders)
        self.assertIn(self.holder_global, holders)
        self.assertNotIn(self.holder_b, holders)

    def test_tenant_b_scoping(self):
        from core.managers import set_current_tenant
        set_current_tenant(self.tenant_b)

        # Tenants
        tenants = list(Tenant.objects.all())
        self.assertIn(self.tenant_b, tenants)
        self.assertNotIn(self.tenant_a, tenants)

        # Sites
        sites = list(Site.objects.all())
        self.assertIn(self.site_b, sites)
        self.assertIn(self.site_global, sites)
        self.assertNotIn(self.site_a, sites)

        # Locations
        locs = list(Location.objects.all())
        self.assertIn(self.loc_b, locs)
        self.assertIn(self.loc_global, locs)
        self.assertNotIn(self.loc_a, locs)

        # AssetHolders
        holders = list(AssetHolder.objects.all())
        self.assertIn(self.holder_b, holders)
        self.assertIn(self.holder_global, holders)
        self.assertNotIn(self.holder_a, holders)

    def test_no_tenant_scoping(self):
        from core.managers import set_current_tenant
        set_current_tenant(None)

        # Tenants
        tenants = list(Tenant.objects.all())
        self.assertIn(self.tenant_a, tenants)
        self.assertIn(self.tenant_b, tenants)

        # Sites
        sites = list(Site.objects.all())
        self.assertIn(self.site_a, sites)
        self.assertIn(self.site_b, sites)
        self.assertIn(self.site_global, sites)

        # Locations
        locs = list(Location.objects.all())
        self.assertIn(self.loc_a, locs)
        self.assertIn(self.loc_b, locs)
        self.assertIn(self.loc_global, locs)

        # AssetHolders
        holders = list(AssetHolder.objects.all())
        self.assertIn(self.holder_a, holders)
        self.assertIn(self.holder_b, holders)
        self.assertIn(self.holder_global, holders)

    def test_tenant_group_sharing(self):
        # Create a TenantGroup
        group = TenantGroup.objects.create(name="Shared Group", slug="shared-group")
        
        # Associate Tenant A and a new Tenant C with the TenantGroup
        self.tenant_a.group = group
        self.tenant_a.save()
        
        tenant_c = Tenant.objects.create(name="Tenant C", slug="tenant-c", group=group)
        
        # Create a site for Tenant C
        site_c = Site.objects.create(name="Site C", slug="site-c", tenant=tenant_c)

        from core.managers import set_current_tenant, set_current_tenant_group
        
        # 1. Under Tenant A context (strict isolation):
        set_current_tenant(self.tenant_a)
        set_current_tenant_group(None)
        
        # Tenant A should strictly only be able to see Tenant A, and NOT Tenant C (even if they share a TenantGroup)
        tenants = list(Tenant.objects.all())
        self.assertIn(self.tenant_a, tenants)
        self.assertNotIn(tenant_c, tenants)
        self.assertNotIn(self.tenant_b, tenants)
        
        # Tenant A should strictly only be able to see Site A and Site Global, but NOT Site C
        sites = list(Site.objects.all())
        self.assertIn(self.site_a, sites)
        self.assertIn(self.site_global, sites)
        self.assertNotIn(site_c, sites)
        self.assertNotIn(self.site_b, sites)

        # 2. Under Tenant Group context (group aggregation):
        set_current_tenant(None)
        set_current_tenant_group(group)

        # The Group should be able to see both Tenant A and Tenant C, but NOT Tenant B
        tenants = list(Tenant.objects.all())
        self.assertIn(self.tenant_a, tenants)
        self.assertIn(tenant_c, tenants)
        self.assertNotIn(self.tenant_b, tenants)

        # The Group should be able to see Site A, Site Global, and Site C, but NOT Site B
        sites = list(Site.objects.all())
        self.assertIn(self.site_a, sites)
        self.assertIn(self.site_global, sites)
        self.assertIn(site_c, sites)
        self.assertNotIn(self.site_b, sites)

        # Clean up context
        set_current_tenant(None)
        set_current_tenant_group(None)


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
        
        # 1. Create a physical AssetHolder that doesn't have a User linked yet
        holder = AssetHolder.objects.create(
            first_name="Beate",
            last_name="Office",
            upn="beate.office",
            email="beate@example.com",
            tenant=self.tenant_a
        )
        self.assertIsNone(holder.user)
        
        # 2. Create the Invitation
        invitation = TenantInvitation.objects.create(
            email="beate@example.com",
            tenant=self.tenant_a,
            role=TenantRole.ADMIN,
            expires_at=timezone.now() + timezone.timedelta(days=7)
        )
        self.assertTrue(invitation.is_valid)
        self.assertEqual(str(invitation), "Invite for beate@example.com to Tenant A")
        
        # 3. Accept the Invitation
        invitee_user = User.objects.create_user(
            username='beate_user', email='beate@example.com', password='password123'
        )
        from organization.models import accept_invitation
        accept_invitation(invitation, invitee_user)
        
        # Assert membership created
        membership = TenantMembership.objects.get(user=invitee_user, tenant=self.tenant_a)
        self.assertEqual(membership.role, TenantRole.ADMIN)
        
        # Assert invitation is no longer valid
        invitation.refresh_from_db()
        self.assertFalse(invitation.is_valid)
        self.assertIsNotNone(invitation.accepted_at)
        
        # Assert AssetHolder is linked to the User profile
        holder.refresh_from_db()
        self.assertEqual(holder.user, invitee_user)

    def test_tenant_membership_backend_permissions(self):
        from organization.models import TenantMembership, TenantRole
        from core.managers import set_current_membership
        
        # Reader role permissions
        reader_mem = TenantMembership.objects.create(
            user=self.user,
            tenant=self.tenant_a,
            role=TenantRole.READER
        )
        
        set_current_membership(reader_mem)
        self.assertTrue(self.user.has_perm('assets.view_asset'))
        self.assertFalse(self.user.has_perm('assets.add_asset'))
        
        # Member role permissions
        reader_mem.role = TenantRole.MEMBER
        reader_mem.save()
        set_current_membership(reader_mem)
        self.assertTrue(self.user.has_perm('assets.view_asset'))
        self.assertTrue(self.user.has_perm('assets.add_asset'))
        self.assertFalse(self.user.has_perm('assets.delete_asset'))
        
        # Admin role permissions
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
        from django.contrib.auth.middleware import AuthenticationMiddleware
        from assetbox.middleware import TenantMiddleware
        from django.test import RequestFactory
        
        # Make a member of Tenant A
        TenantMembership.objects.create(
            user=self.user,
            tenant=self.tenant_a,
            role=TenantRole.MEMBER
        )
        
        factory = RequestFactory()
        
        # 1. No switch param: defaults to first membership (Tenant A)
        request = factory.get('/')
        request.user = self.user
        
        # Add session support manually to request
        middleware = SessionMiddleware(lambda r: None)
        middleware.process_request(request)
        
        tenant_middleware = TenantMiddleware(lambda r: None)
        tenant_middleware.process_request(request)
        
        self.assertEqual(request.active_tenant, self.tenant_a)
        self.assertEqual(request.session['active_tenant_id'], self.tenant_a.id)
        
        # 2. Switch to another tenant where user has NO membership
        request = factory.get('/?switch_tenant={}'.format(self.tenant_b.id))
        request.user = self.user
        middleware.process_request(request)
        tenant_middleware.process_request(request)
        
        # Should fallback to Tenant A since they have no membership for Tenant B
        self.assertEqual(request.active_tenant, self.tenant_a)



