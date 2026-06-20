"""B2 regression: Asset FK choice fields must be tenant-scoped.

`location` / `cost_center` / `purchase_order_line` on AssetForm were import-frozen
unscoped querysets, exposing every tenant's rows and allowing cross-tenant FK
assignment. The form now rescopes them per request, and Asset.clean() rejects a
FK that belongs to a different tenant (defence-in-depth, also covers API/import).
"""
from django.test import TestCase
from django.core.exceptions import ValidationError
from model_bakery import baker

from organization.models import Tenant, Location, Site
from assets.models import Asset, StatusLabel
from assets.forms.asset_form import AssetForm
from core.tests.mixins import TenantTestMixin


class AssetFkTenantScopingTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name='Tenant A', slug='tenant-a')
        self.tenant_b = Tenant.objects.create(name='Tenant B', slug='tenant-b')
        self.site = Site.objects.create(name='HQ', slug='hq')
        self.loc_a = Location.objects.create(name='Loc A', slug='loc-a', site=self.site, tenant=self.tenant)
        self.loc_b = Location.objects.create(name='Loc B', slug='loc-b', site=self.site, tenant=self.tenant_b)
        self.cc_b = baker.make('organization.CostCenter', tenant=self.tenant_b)
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)

    def test_clean_rejects_cross_tenant_location(self):
        asset = Asset(name='X', asset_tag='X-1', tenant=self.tenant, location=self.loc_b, status=self.status)
        with self.assertRaises(ValidationError) as ctx:
            asset.clean()
        self.assertIn('location', ctx.exception.message_dict)

    def test_clean_rejects_cross_tenant_cost_center(self):
        asset = Asset(name='X', asset_tag='X-2', tenant=self.tenant, cost_center=self.cc_b, status=self.status)
        with self.assertRaises(ValidationError) as ctx:
            asset.clean()
        self.assertIn('cost_center', ctx.exception.message_dict)

    def test_clean_allows_same_tenant_location(self):
        asset = Asset(name='X', asset_tag='X-3', tenant=self.tenant, location=self.loc_a, status=self.status)
        try:
            asset.clean()
        except ValidationError as exc:
            self.fail(f"same-tenant location should be valid, got {exc.message_dict}")

    def test_form_location_queryset_excludes_other_tenant(self):
        self.set_active_tenant(self.tenant)
        form = AssetForm()
        ids = set(form.fields['location'].queryset.values_list('pk', flat=True))
        self.assertIn(self.loc_a.pk, ids)
        self.assertNotIn(self.loc_b.pk, ids)
