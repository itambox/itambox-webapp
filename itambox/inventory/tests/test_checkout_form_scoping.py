"""Systemic B1/B2-class follow-up: inventory checkout forms must tenant-scope
their FK choice fields.

BaseCheckoutForm only rescoped `assigned_holder`; `assigned_location`,
`assigned_asset` and the subclass source fields (`source_location` /
`from_location`) stayed import-frozen and unscoped, letting a checkout target —
and expose in the dropdown — another tenant's location or asset.
"""
from django.test import TestCase
from model_bakery import baker

from organization.models import Tenant, Location, Site, AssetHolder
from assets.models import Asset, StatusLabel
from inventory.models import Accessory
from inventory.forms import AccessoryCheckoutForm
from core.tests.mixins import TenantTestMixin


class CheckoutFormTenantScopingTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name='Tenant A', slug='cof-a')
        self.tenant_b = Tenant.objects.create(name='Tenant B', slug='cof-b')
        self.set_active_tenant(self.tenant)
        self.site = Site.objects.create(name='Shared', slug='cof-shared')  # global site
        self.accessory = baker.make(Accessory, tenant=self.tenant)
        self.loc_a = Location.objects.create(name='Loc A', slug='cof-la', site=self.site, tenant=self.tenant)
        self.loc_b = Location.objects.create(name='Loc B', slug='cof-lb', site=self.site, tenant=self.tenant_b)
        self.holder_b = baker.make(AssetHolder, tenant=self.tenant_b)
        status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        self.asset_b = baker.make(Asset, tenant=self.tenant_b, asset_tag='COF-B', status=status)

    def test_checkout_form_scopes_fk_to_item_tenant(self):
        self.clear_tenant_context()
        form = AccessoryCheckoutForm(accessory=self.accessory)

        for field in ('assigned_location', 'from_location'):
            ids = set(form.fields[field].queryset.values_list('pk', flat=True))
            self.assertIn(self.loc_a.pk, ids, field)
            self.assertNotIn(self.loc_b.pk, ids, field)

        asset_ids = set(form.fields['assigned_asset'].queryset.values_list('pk', flat=True))
        self.assertNotIn(self.asset_b.pk, asset_ids)

        holder_ids = set(form.fields['assigned_holder'].queryset.values_list('pk', flat=True))
        self.assertNotIn(self.holder_b.pk, holder_ids)
