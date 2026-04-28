from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.db import IntegrityError
import json

from extras.models import ConfigContext
from extras.utils import deep_merge, get_context_for_asset
from organization.models import Region, Site, Location, Tenant
from assets.models import Asset, StatusLabel, AssetType, Manufacturer

User = get_user_model()


class ConfigContextUtilityTests(TestCase):
    def test_deep_merge_basic(self):
        dict_a = {'a': 1, 'b': {'x': 10}}
        dict_b = {'b': {'y': 20}, 'c': 3}
        merged = deep_merge(dict_a, dict_b)
        expected = {'a': 1, 'b': {'x': 10, 'y': 20}, 'c': 3}
        self.assertEqual(merged, expected)

    def test_deep_merge_override(self):
        dict_a = {'a': 1, 'b': {'x': 10}}
        dict_b = {'b': 'not-a-dict', 'c': 3}
        merged = deep_merge(dict_a, dict_b)
        expected = {'a': 1, 'b': 'not-a-dict', 'c': 3}
        self.assertEqual(merged, expected)


class ConfigContextHierarchyTests(TestCase):
    def setUp(self):
        # Create core hierarchy
        self.parent_region = Region.objects.create(name='Global', slug='global')
        self.child_region = Region.objects.create(name='Europe', slug='europe', parent=self.parent_region)

        self.tenant = Tenant.objects.create(name='Tenant Inc', slug='tenant-inc')

        self.site = Site.objects.create(
            name='Paris DataCenter', slug='paris-dc', region=self.child_region, tenant=self.tenant
        )

        self.parent_location = Location.objects.create(
            name='Building A', slug='building-a', site=self.site, tenant=self.tenant
        )
        self.child_location = Location.objects.create(
            name='Room 101', slug='room-101', site=self.site, parent=self.parent_location, tenant=self.tenant
        )

        # Create status label
        self.status = StatusLabel.objects.create(name='Active', slug='active', type='deployed')

        # Create asset prerequisites
        self.manufacturer = Manufacturer.objects.create(name='Dell', slug='dell')
        self.asset_type = AssetType.objects.create(manufacturer=self.manufacturer, model='PowerEdge R740')

        # Create asset at leaf location
        self.asset = Asset.objects.create(
            name='paris-srv-01',
            asset_tag='ASSET-000001',
            asset_type=self.asset_type,
            status=self.status,
            location=self.child_location,
            tenant=self.tenant
        )

    def test_get_context_no_contexts(self):
        # With no ConfigContexts configured, resolving should return empty dictionary
        context = get_context_for_asset(self.asset)
        self.assertEqual(context, {})

    def test_hierarchical_traversal_and_merging(self):
        # Create contexts at different levels
        # 1. Tenant context
        cc_tenant = ConfigContext.objects.create(
            name='Tenant Context',
            weight=10,
            data={'dns': {'nameservers': ['8.8.8.8'], 'domain': 'tenant.internal'}, 'theme': 'blue'}
        )
        cc_tenant.tenants.add(self.tenant)

        # 2. Parent Region context
        cc_global = ConfigContext.objects.create(
            name='Global Context',
            weight=20,
            data={'dns': {'search': ['global.internal']}, 'ntp': ['pool.ntp.org']}
        )
        cc_global.regions.add(self.parent_region)

        # 3. Child Region context
        cc_europe = ConfigContext.objects.create(
            name='Europe Context',
            weight=30,
            data={'dns': {'nameservers': ['1.1.1.1']}, 'europe_setting': True}
        )
        cc_europe.regions.add(self.child_region)

        # 4. Site context
        cc_site = ConfigContext.objects.create(
            name='Paris DC Context',
            weight=40,
            data={'dns': {'domain': 'paris.internal'}, 'timezone': 'Europe/Paris'}
        )
        cc_site.sites.add(self.site)

        # 5. Parent Location context
        cc_bld = ConfigContext.objects.create(
            name='Building A Context',
            weight=50,
            data={'rack_monitoring': False}
        )
        cc_bld.locations.add(self.parent_location)

        # 6. Child Location context
        cc_room = ConfigContext.objects.create(
            name='Room 101 Context',
            weight=60,
            data={'rack_monitoring': True, 'rack_unit': 12}
        )
        cc_room.locations.add(self.child_location)

        # Resolve context for Paris server
        context = get_context_for_asset(self.asset)

        # Assert correct recursive merging and values
        self.assertEqual(context['theme'], 'blue')
        self.assertEqual(context['europe_setting'], True)
        self.assertEqual(context['timezone'], 'Europe/Paris')
        self.assertEqual(context['rack_monitoring'], True) # Overridden by Room 101 (weight 60 > 50)
        self.assertEqual(context['rack_unit'], 12)
        self.assertEqual(context['ntp'], ['pool.ntp.org'])

        # Nested dictionary validation
        dns = context['dns']
        self.assertEqual(dns['search'], ['global.internal'])
        self.assertEqual(dns['nameservers'], ['1.1.1.1']) # Tenant is weight 10, Global region doesn't override it, but Europe region (weight 30) overrides Tenant nameserver
        self.assertEqual(dns['domain'], 'paris.internal') # Site (weight 40) overrides Tenant domain (weight 10)

    def test_precedence_weights(self):
        # Two overlapping contexts at same Region
        cc_low = ConfigContext.objects.create(
            name='Low Priority Global',
            weight=100,
            data={'setting': 'low'}
        )
        cc_low.regions.add(self.parent_region)

        cc_high = ConfigContext.objects.create(
            name='High Priority Global',
            weight=200,
            data={'setting': 'high'}
        )
        cc_high.regions.add(self.parent_region)

        context = get_context_for_asset(self.asset)
        self.assertEqual(context['setting'], 'high')


class ConfigContextViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')
        self.cc = ConfigContext.objects.create(
            name='Default Config',
            weight=100,
            data={'dns': '8.8.8.8'}
        )

    def test_list_view(self):
        url = reverse('extras:configcontext_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Default Config')

    def test_create_view_get(self):
        url = reverse('extras:configcontext_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post_valid_json(self):
        url = reverse('extras:configcontext_create')
        response = self.client.post(url, {
            'name': 'New Valid Context',
            'description': 'Valid configuration context',
            'weight': 150,
            'data': '{"ntp": "10.0.0.1", "nested": {"val": true}}'
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ConfigContext.objects.filter(name='New Valid Context').exists())
        new_cc = ConfigContext.objects.get(name='New Valid Context')
        self.assertEqual(new_cc.data['ntp'], '10.0.0.1')
        self.assertTrue(new_cc.data['nested']['val'])

    def test_create_view_post_invalid_json(self):
        url = reverse('extras:configcontext_create')
        response = self.client.post(url, {
            'name': 'New Invalid Context',
            'description': 'Invalid configuration context',
            'weight': 150,
            'data': '{"ntp": "10.0.0.1", INVALID_JSON}'
        })
        self.assertEqual(response.status_code, 200)
        form = response.context.get('form')
        self.assertIsNotNone(form)
        self.assertTrue(form.has_error('data'))
        self.assertFalse(ConfigContext.objects.filter(name='New Invalid Context').exists())

    def test_edit_view_get(self):
        url = reverse('extras:configcontext_edit', kwargs={'pk': self.cc.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_edit_view_post_valid_json(self):
        url = reverse('extras:configcontext_edit', kwargs={'pk': self.cc.pk})
        response = self.client.post(url, {
            'name': 'Updated Config',
            'weight': 300,
            'data': '{"dns": "1.1.1.1"}'
        })
        self.assertEqual(response.status_code, 302)
        self.cc.refresh_from_db()
        self.assertEqual(self.cc.name, 'Updated Config')
        self.assertEqual(self.cc.weight, 300)
        self.assertEqual(self.cc.data['dns'], '1.1.1.1')

    def test_delete_view_get(self):
        url = reverse('extras:configcontext_delete', kwargs={'pk': self.cc.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_delete_view_post(self):
        url = reverse('extras:configcontext_delete', kwargs={'pk': self.cc.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ConfigContext.objects.filter(pk=self.cc.pk).exists())
