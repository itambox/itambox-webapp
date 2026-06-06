from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from organization.models import Region, Site, Location, SiteGroup

User = get_user_model()

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

    def test_list_view_counts(self):
        from assets.models import Asset, AssetType, Manufacturer, AssetRole, StatusLabel
        # Setup required metadata models for creating assets
        manufacturer = Manufacturer.objects.create(name="Dell", slug="dell")
        role = AssetRole.objects.create(name="Workstation", slug="workstation")
        status = StatusLabel.objects.create(name="Ready", slug="ready", type=StatusLabel.TYPE_DEPLOYABLE)
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Latitude 5520",
            slug="latitude-5520"
        )
        
        # Create locations for the site
        loc1 = Location.objects.create(name="Location 1", slug="loc-1", site=self.site)
        loc2 = Location.objects.create(name="Location 2", slug="loc-2", site=self.site)
        
        # Create assets in those locations
        Asset.objects.create(
            name="Asset 1",
            asset_tag="TAG-1",
            asset_type=asset_type,
            asset_role=role,
            status=status,
            location=loc1
        )
        Asset.objects.create(
            name="Asset 2",
            asset_tag="TAG-2",
            asset_type=asset_type,
            asset_role=role,
            status=status,
            location=loc2
        )
        Asset.objects.create(
            name="Asset 3",
            asset_tag="TAG-3",
            asset_type=asset_type,
            asset_role=role,
            status=status,
            location=loc2
        )
        
        url = reverse('organization:site_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        
        # Get the site object from the table's annotated queryset
        table = response.context['table']
        site_obj = None
        for row in table.data:
            if row.pk == self.site.pk:
                site_obj = row
                break
        
        self.assertIsNotNone(site_obj)
        self.assertEqual(site_obj.location_count, 2)
        self.assertEqual(site_obj.asset_count, 3)

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
