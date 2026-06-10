"""Tests for assets/scanning.py and the /scan/resolve/ endpoint."""
import json

from django.test import TestCase, RequestFactory
from django.urls import reverse
from django.contrib.auth import get_user_model

from assets.models import Asset, AssetType, StatusLabel, AssetRole, Manufacturer
from assets.scanning import resolve_scanned_code
from core.tests.mixins import TenantTestMixin
from organization.models import Tenant, TenantMembership, TenantRole

User = get_user_model()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_asset_fixtures():
    mfr = Manufacturer.objects.create(name="TestMfr", slug="testmfr")
    role = AssetRole.objects.create(name="TestRole", slug="testrole")
    status = StatusLabel.objects.create(
        name="Active", slug="active-scan-test", type=StatusLabel.TYPE_DEPLOYABLE
    )
    atype = AssetType.objects.create(
        manufacturer=mfr, model="TestModel", slug="test-model", requestable=False
    )
    return role, status, atype


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests for resolve_scanned_code
# ─────────────────────────────────────────────────────────────────────────────

class ResolveScannedCodeTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context()
        self.set_active_tenant(self.tenant, self.tenant_membership)
        role, status, atype = _make_asset_fixtures()
        self.asset = Asset.objects.create(
            name="Scan Test Laptop",
            asset_tag="ITM-00001",
            serial_number="SN-LAPTOP-99",
            asset_type=atype,
            asset_role=role,
            status=status,
            tenant=self.tenant,
        )

    def test_bare_asset_tag(self):
        result = resolve_scanned_code("ITM-00001")
        self.assertEqual(result, self.asset)

    def test_bare_serial_number(self):
        result = resolve_scanned_code("SN-LAPTOP-99")
        self.assertEqual(result, self.asset)

    def test_itambox_scheme_tag(self):
        result = resolve_scanned_code("itambox:ITM-00001")
        self.assertEqual(result, self.asset)

    def test_itambox_asset_pk_url(self):
        result = resolve_scanned_code(f"itambox://asset/{self.asset.pk}")
        self.assertEqual(result, self.asset)

    def test_full_http_url(self):
        # Simulate a label that encoded a full URL (legacy or external QR)
        url = f"https://itam.example.com/assets/{self.asset.pk}/"
        result = resolve_scanned_code(url)
        self.assertEqual(result, self.asset)

    def test_url_with_tag_segment(self):
        # URL whose last segment is an asset tag (not a numeric pk)
        url = f"http://localhost:8000/assets/ITM-00001/"
        result = resolve_scanned_code(url)
        self.assertEqual(result, self.asset)

    def test_unknown_code_returns_none(self):
        result = resolve_scanned_code("NO-SUCH-TAG")
        self.assertIsNone(result)

    def test_empty_string_returns_none(self):
        result = resolve_scanned_code("")
        self.assertIsNone(result)

    def test_whitespace_stripped(self):
        result = resolve_scanned_code("  ITM-00001  ")
        self.assertEqual(result, self.asset)


# ─────────────────────────────────────────────────────────────────────────────
# ScanResolveView endpoint tests
# ─────────────────────────────────────────────────────────────────────────────

class ScanResolveViewTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="TenantA", slug="tenant-a")
        role, status, atype = _make_asset_fixtures()
        self.asset = Asset.objects.create(
            name="Scan Endpoint Asset",
            asset_tag="SCAN-001",
            serial_number="SN-SCAN-001",
            asset_type=atype,
            asset_role=role,
            status=status,
            tenant=self.tenant,
        )
        self.url = reverse("scan_resolve")

    def _login(self):
        self.client_login_to_tenant(self.tenant_admin, self.tenant)

    def test_found_by_tag(self):
        self._login()
        resp = self.client.get(self.url, {"code": "SCAN-001"})
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["found"])
        self.assertIn("/assets/", data["url"])

    def test_found_by_serial(self):
        self._login()
        resp = self.client.get(self.url, {"code": "SN-SCAN-001"})
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["found"])

    def test_found_by_itambox_scheme(self):
        self._login()
        resp = self.client.get(self.url, {"code": f"itambox:SCAN-001"})
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["found"])

    def test_found_by_url_wrapped(self):
        self._login()
        resp = self.client.get(self.url, {"code": f"https://itam.example.com/assets/{self.asset.pk}/"})
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["found"])

    def test_not_found(self):
        self._login()
        resp = self.client.get(self.url, {"code": "NOPE-9999"})
        self.assertEqual(resp.status_code, 404)
        data = json.loads(resp.content)
        self.assertFalse(data["found"])

    def test_missing_code_returns_400(self):
        self._login()
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 400)

    def test_unauthenticated_redirects(self):
        resp = self.client.get(self.url, {"code": "SCAN-001"})
        self.assertIn(resp.status_code, (302, 403))

    def test_cross_tenant_isolation(self):
        """A user in TenantB must not see TenantA's assets."""
        self.setup_tenant_context(name="TenantB", slug="tenant-b")
        # tenant_admin is now TenantB's admin (setup_tenant_context overwrites self.tenant etc.)
        self.client_login_to_tenant(self.tenant_admin, self.tenant)
        resp = self.client.get(self.url, {"code": "SCAN-001"})
        # TenantB context — asset belongs to TenantA, must not be visible
        self.assertNotEqual(resp.status_code, 200)
        if resp.status_code == 200:
            data = json.loads(resp.content)
            self.assertFalse(data.get("found"))
