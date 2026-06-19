"""Tests for the EAN field + barcode-scanner resolution.

Scanning an asset-type EAN returns the asset list filtered by that EAN; scanning
a component/accessory/consumable EAN goes to that item's detail. Resolution is
tenant-scoped and permission-gated.
"""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assets.filters import AssetFilterSet
from assets.models import Asset, AssetRole, AssetType, Manufacturer, StatusLabel
from core.tests.mixins import TenantTestMixin
from inventory.models import Accessory, Component, Consumable

User = get_user_model()


class EanScanTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(slug="ean")
        self.set_active_tenant(self.tenant, self.tenant_membership)
        self.mfr = Manufacturer.objects.create(name="EANMfr", slug="eanmfr")
        self.role = AssetRole.objects.create(name="EANRole", slug="eanrole")
        self.status = StatusLabel.objects.create(name="Avail", slug="ean-avail", type="deployable")
        self.atype = AssetType.objects.create(
            manufacturer=self.mfr, model="EAN Model", slug="ean-model", ean="4012345678901",
        )
        self.asset = Asset.objects.create(
            name="EAN Asset", asset_tag="EAN-A1", asset_type=self.atype,
            asset_role=self.role, status=self.status, tenant=self.tenant,
        )
        self.component = Component.objects.create(name="RAM", slug="ram-ean", manufacturer=self.mfr, ean="1111111111116", tenant=self.tenant)
        self.accessory = Accessory.objects.create(name="Mouse", slug="mouse-ean", manufacturer=self.mfr, ean="2222222222226", tenant=self.tenant)
        self.consumable = Consumable.objects.create(name="Toner", slug="toner-ean", manufacturer=self.mfr, ean="3333333333336", tenant=self.tenant)
        self.url = reverse("scan_resolve")

    def _resolve(self, code):
        resp = self.client.get(self.url, {"code": code})
        return resp, (json.loads(resp.content) if resp.content else {})

    def test_assettype_ean_returns_filtered_asset_list(self):
        self.client_login_to_tenant(self.tenant_admin, self.tenant)
        resp, data = self._resolve("4012345678901")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data["found"])
        self.assertIn("/assets/", data["url"])
        self.assertIn("ean=4012345678901", data["url"])

    def test_component_ean_returns_detail(self):
        self.client_login_to_tenant(self.tenant_admin, self.tenant)
        _, data = self._resolve("1111111111116")
        self.assertEqual(data["url"], self.component.get_absolute_url())

    def test_accessory_ean_returns_detail(self):
        self.client_login_to_tenant(self.tenant_admin, self.tenant)
        _, data = self._resolve("2222222222226")
        self.assertEqual(data["url"], self.accessory.get_absolute_url())

    def test_consumable_ean_returns_detail(self):
        self.client_login_to_tenant(self.tenant_admin, self.tenant)
        _, data = self._resolve("3333333333336")
        self.assertEqual(data["url"], self.consumable.get_absolute_url())

    def test_unknown_ean_not_found(self):
        self.client_login_to_tenant(self.tenant_admin, self.tenant)
        resp, data = self._resolve("9999999999994")
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(data["found"])

    def test_asset_tag_still_resolves_first(self):
        self.client_login_to_tenant(self.tenant_admin, self.tenant)
        _, data = self._resolve("EAN-A1")
        self.assertEqual(data["url"], self.asset.get_absolute_url())

    def test_component_ean_gated_by_permission(self):
        # view_asset but NOT inventory.view_component → component EAN must not resolve.
        self.tenant_role.permissions = ["assets.view_asset"]
        self.tenant_role.save()
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        resp, data = self._resolve("1111111111116")
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(data["found"])

    def test_cross_tenant_component_not_resolved(self):
        from organization.models import Tenant
        other = Tenant.objects.create(name="OtherEan", slug="other-ean")
        Component.objects.create(name="Other RAM", slug="other-ram-ean", manufacturer=self.mfr, ean="7777777777776", tenant=other)
        self.client_login_to_tenant(self.tenant_admin, self.tenant)  # superuser resolves global; check scoped member instead
        self.tenant_role.permissions = ["assets.view_asset", "inventory.view_component"]
        self.tenant_role.save()
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        resp, data = self._resolve("7777777777776")
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(data["found"])

    def test_asset_ean_filter(self):
        qs = AssetFilterSet({"ean": "4012345678901"}, queryset=Asset.objects.all()).qs
        self.assertIn(self.asset, qs)
        qs_none = AssetFilterSet({"ean": "0000"}, queryset=Asset.objects.all()).qs
        self.assertNotIn(self.asset, qs_none)
