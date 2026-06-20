"""Systemic B1/B2-class follow-up: inventory stock/allocation/kit forms must
tenant-scope their FK choice fields.

These forms exposed import-frozen unscoped FK dropdowns (component/accessory/
consumable/location/holder/asset/kit/license), letting a member pick — and see —
another tenant's object. Each form now rescopes per request to the active tenant.
"""
from django.test import TestCase
from model_bakery import baker

from organization.models import Tenant, Location, Site, AssetHolder
from assets.models import Asset, StatusLabel, Manufacturer
from inventory.models import Component, Accessory, Consumable, Kit
from inventory.forms import (
    ComponentStockForm, ComponentAllocationForm, ComponentStockModalForm,
    AccessoryStockForm, ConsumableStockForm, KitItemForm,
)
from licenses.models import License
from core.tests.mixins import TenantTestMixin


class InventoryFormFkScopingTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name='Tenant A', slug='iffk-a')
        self.tenant_b = Tenant.objects.create(name='Tenant B', slug='iffk-b')
        site = baker.make(Site, tenant=self.tenant)
        mfr = baker.make(Manufacturer)
        status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)

        # own-tenant (A) — must remain selectable
        self.loc_a = baker.make(Location, tenant=self.tenant, site=site)
        self.component_a = baker.make(Component, tenant=self.tenant)
        self.accessory_a = baker.make(Accessory, tenant=self.tenant)
        self.consumable_a = baker.make(Consumable, tenant=self.tenant)
        self.kit_a = baker.make(Kit, tenant=self.tenant)

        # other-tenant (B) — must NOT appear
        self.loc_b = baker.make(Location, tenant=self.tenant_b, site=site)
        self.holder_b = baker.make(AssetHolder, tenant=self.tenant_b)
        self.asset_b = baker.make(Asset, tenant=self.tenant_b, asset_tag='IFFK-B', status=status)
        self.component_b = baker.make(Component, tenant=self.tenant_b)
        self.accessory_b = baker.make(Accessory, tenant=self.tenant_b)
        self.consumable_b = baker.make(Consumable, tenant=self.tenant_b)
        self.kit_b = baker.make(Kit, tenant=self.tenant_b)
        software_b = baker.make('software.Software', tenant=self.tenant_b, manufacturer=mfr)
        self.license_b = baker.make(License, tenant=self.tenant_b, software=software_b)

        self.set_active_tenant(self.tenant)  # ambient tenant for form instantiation

    def _pks(self, form, field):
        return set(form.fields[field].queryset.values_list('pk', flat=True))

    def test_component_stock_form(self):
        pks_c = self._pks(ComponentStockForm(), 'component')
        self.assertIn(self.component_a.pk, pks_c)
        self.assertNotIn(self.component_b.pk, pks_c)
        self.assertNotIn(self.loc_b.pk, self._pks(ComponentStockForm(), 'location'))
        self.assertIn(self.loc_a.pk, self._pks(ComponentStockForm(), 'location'))

    def test_component_allocation_form(self):
        form = ComponentAllocationForm()
        cases = [
            ('component', self.component_b.pk), ('assigned_holder', self.holder_b.pk),
            ('assigned_location', self.loc_b.pk), ('assigned_asset', self.asset_b.pk),
            ('from_location', self.loc_b.pk),
        ]
        for field, pk in cases:
            self.assertNotIn(pk, self._pks(form, field), field)

    def test_component_stock_modal_form(self):
        self.assertNotIn(self.loc_b.pk, self._pks(ComponentStockModalForm(), 'location'))

    def test_accessory_stock_form(self):
        form = AccessoryStockForm()
        self.assertIn(self.accessory_a.pk, self._pks(form, 'accessory'))
        self.assertNotIn(self.accessory_b.pk, self._pks(form, 'accessory'))
        self.assertNotIn(self.loc_b.pk, self._pks(form, 'location'))

    def test_consumable_stock_form(self):
        form = ConsumableStockForm()
        self.assertIn(self.consumable_a.pk, self._pks(form, 'consumable'))
        self.assertNotIn(self.consumable_b.pk, self._pks(form, 'consumable'))
        self.assertNotIn(self.loc_b.pk, self._pks(form, 'location'))

    def test_kit_item_form(self):
        form = KitItemForm()
        self.assertIn(self.kit_a.pk, self._pks(form, 'kit'))
        self.assertNotIn(self.kit_b.pk, self._pks(form, 'kit'))
        self.assertNotIn(self.accessory_b.pk, self._pks(form, 'accessory'))
        self.assertNotIn(self.consumable_b.pk, self._pks(form, 'consumable'))
        self.assertNotIn(self.license_b.pk, self._pks(form, 'license'))
