from django.test import TestCase
from django.contrib.auth import get_user_model
from organization.models import Region, Site, Location, SiteGroup, TenantGroup, Tenant, AssetHolder

User = get_user_model()

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

        self.site_a = Site.objects.create(name="Site A", slug="site-a", tenant=self.tenant_a)
        self.site_b = Site.objects.create(name="Site B", slug="site-b", tenant=self.tenant_b)
        self.site_global = Site.objects.create(name="Site Global", slug="site-global", tenant=None)

        self.loc_a = Location.objects.create(name="Location A", slug="loc-a", site=self.site_a, tenant=self.tenant_a)
        self.loc_b = Location.objects.create(name="Location B", slug="loc-b", site=self.site_b, tenant=self.tenant_b)
        self.loc_global = Location.objects.create(name="Location Global", slug="loc-global", site=self.site_global, tenant=None)

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

        tenants = list(Tenant.objects.all())
        self.assertIn(self.tenant_a, tenants)
        self.assertNotIn(self.tenant_b, tenants)

        sites = list(Site.objects.all())
        self.assertIn(self.site_a, sites)
        self.assertNotIn(self.site_global, sites)
        self.assertNotIn(self.site_b, sites)

        locs = list(Location.objects.all())
        self.assertIn(self.loc_a, locs)
        self.assertNotIn(self.loc_global, locs)
        self.assertNotIn(self.loc_b, locs)

        holders = list(AssetHolder.objects.all())
        self.assertIn(self.holder_a, holders)
        self.assertNotIn(self.holder_global, holders)
        self.assertNotIn(self.holder_b, holders)

    def test_tenant_b_scoping(self):
        from core.managers import set_current_tenant
        set_current_tenant(self.tenant_b)

        tenants = list(Tenant.objects.all())
        self.assertIn(self.tenant_b, tenants)
        self.assertNotIn(self.tenant_a, tenants)

        sites = list(Site.objects.all())
        self.assertIn(self.site_b, sites)
        self.assertNotIn(self.site_global, sites)
        self.assertNotIn(self.site_a, sites)

        locs = list(Location.objects.all())
        self.assertIn(self.loc_b, locs)
        self.assertNotIn(self.loc_global, locs)
        self.assertNotIn(self.loc_a, locs)

        holders = list(AssetHolder.objects.all())
        self.assertIn(self.holder_b, holders)
        self.assertNotIn(self.holder_global, holders)
        self.assertNotIn(self.holder_a, holders)

    def test_no_tenant_scoping(self):
        from core.managers import set_current_tenant
        set_current_tenant(None)

        tenants = list(Tenant.objects.all())
        self.assertIn(self.tenant_a, tenants)
        self.assertIn(self.tenant_b, tenants)

        sites = list(Site.objects.all())
        self.assertIn(self.site_a, sites)
        self.assertIn(self.site_b, sites)
        self.assertIn(self.site_global, sites)

        locs = list(Location.objects.all())
        self.assertIn(self.loc_a, locs)
        self.assertIn(self.loc_b, locs)
        self.assertIn(self.loc_global, locs)

        holders = list(AssetHolder.objects.all())
        self.assertIn(self.holder_a, holders)
        self.assertIn(self.holder_b, holders)
        self.assertIn(self.holder_global, holders)

    def test_tenant_group_sharing(self):
        group = TenantGroup.objects.create(name="Shared Group", slug="shared-group")
        
        self.tenant_a.group = group
        self.tenant_a.save()
        
        tenant_c = Tenant.objects.create(name="Tenant C", slug="tenant-c", group=group)
        site_c = Site.objects.create(name="Site C", slug="site-c", tenant=tenant_c)

        from core.managers import set_current_tenant, set_current_tenant_group
        
        set_current_tenant(self.tenant_a)
        set_current_tenant_group(None)
        
        tenants = list(Tenant.objects.all())
        self.assertIn(self.tenant_a, tenants)
        self.assertNotIn(tenant_c, tenants)
        self.assertNotIn(self.tenant_b, tenants)
        
        sites = list(Site.objects.all())
        self.assertIn(self.site_a, sites)
        self.assertNotIn(self.site_global, sites)
        self.assertNotIn(site_c, sites)
        self.assertNotIn(self.site_b, sites)

        set_current_tenant(None)
        set_current_tenant_group(group)

        tenants = list(Tenant.objects.all())
        self.assertIn(self.tenant_a, tenants)
        self.assertIn(tenant_c, tenants)
        self.assertNotIn(self.tenant_b, tenants)

        sites = list(Site.objects.all())
        self.assertIn(self.site_a, sites)
        self.assertNotIn(self.site_global, sites)
        self.assertIn(site_c, sites)
        self.assertNotIn(self.site_b, sites)

        set_current_tenant(None)
        set_current_tenant_group(None)
