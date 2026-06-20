"""B1/B2-class follow-up: LocationForm (site, parent) and CostCenterForm (parent)
must tenant-scope their FK choice fields.

(SiteForm's region/group target the global Region/SiteGroup models, so they are
not cross-tenant leaks and are intentionally left unscoped.)
"""
from django.test import TestCase
from model_bakery import baker

from organization.models import Tenant, Location, Site, CostCenter
from organization.forms import LocationForm, CostCenterForm
from core.tests.mixins import TenantTestMixin


class OrganizationFormFkScopingTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name='Tenant A', slug='offk-a')
        self.tenant_b = Tenant.objects.create(name='Tenant B', slug='offk-b')
        self.site_a = baker.make(Site, tenant=self.tenant)
        self.site_b = baker.make(Site, tenant=self.tenant_b)
        self.loc_a = baker.make(Location, tenant=self.tenant, site=self.site_a)
        self.loc_b = baker.make(Location, tenant=self.tenant_b, site=self.site_b)
        self.cc_a = baker.make(CostCenter, tenant=self.tenant)
        self.cc_b = baker.make(CostCenter, tenant=self.tenant_b)
        self.set_active_tenant(self.tenant)

    def test_location_form_site_and_parent_scoped(self):
        form = LocationForm()
        site_pks = set(form.fields['site'].queryset.values_list('pk', flat=True))
        self.assertIn(self.site_a.pk, site_pks)
        self.assertNotIn(self.site_b.pk, site_pks)
        parent_pks = set(form.fields['parent'].queryset.values_list('pk', flat=True))
        self.assertIn(self.loc_a.pk, parent_pks)
        self.assertNotIn(self.loc_b.pk, parent_pks)

    def test_costcenter_form_parent_scoped(self):
        pks = set(CostCenterForm().fields['parent'].queryset.values_list('pk', flat=True))
        self.assertIn(self.cc_a.pk, pks)
        self.assertNotIn(self.cc_b.pk, pks)
