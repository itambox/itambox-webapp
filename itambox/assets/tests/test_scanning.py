"""Tests for assets/scanning.py and the /scan/resolve/ endpoint."""
import json

from django.test import TestCase, RequestFactory
from django.urls import reverse
from django.contrib.auth import get_user_model

from assets.models import Asset, AssetType, StatusLabel, AssetRole, Manufacturer
from assets.scanning import resolve_scanned_code
from core.tests.mixins import TenantTestMixin
from organization.models import Tenant, Membership, Role

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

    def test_case_insensitive_asset_tag(self):
        result = resolve_scanned_code("itm-00001")
        self.assertEqual(result, self.asset)

    def test_case_insensitive_serial_number(self):
        result = resolve_scanned_code("sn-laptop-99")
        self.assertEqual(result, self.asset)

    def test_case_insensitive_itambox_scheme_tag(self):
        result = resolve_scanned_code("itambox:itm-00001")
        self.assertEqual(result, self.asset)

    def test_itambox_double_slash_tag(self):
        result = resolve_scanned_code("itambox://itm-00001")
        self.assertEqual(result, self.asset)

    def test_itambox_nested_url(self):
        result = resolve_scanned_code(f"itambox:https://itam.example.com/assets/{self.asset.pk}/")
        self.assertEqual(result, self.asset)

    def test_whitespace_and_slashes_stripped(self):
        result = resolve_scanned_code("  itambox://itm-00001/  ")
        self.assertEqual(result, self.asset)

    def test_enclosed_in_quotes(self):
        result = resolve_scanned_code('"itambox:ITM-00001"')
        self.assertEqual(result, self.asset)

    def test_full_width_colon(self):
        result = resolve_scanned_code("itambox：ITM-00001")
        self.assertEqual(result, self.asset)

    def test_bom_and_zero_width_space(self):
        result = resolve_scanned_code("\ufeffitambox:\u200bITM-00001")
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

    def test_no_active_tenant_returns_404_no_leak(self):
        """User authenticated but no active tenant → fail closed (404), no cross-tenant data."""
        # Log in without setting active_tenant_id in session — TenantMiddleware leaves tenant None.
        no_tenant_user = User.objects.create_user(
            username="notenant", email="notenant@example.com", password="password"
        )
        self.client.force_login(no_tenant_user)
        # Deliberately omit session['active_tenant_id'] so TenantMiddleware finds no tenant.
        resp = self.client.get(self.url, {"code": "SCAN-001"})
        self.assertEqual(resp.status_code, 404)
        data = json.loads(resp.content)
        self.assertFalse(data.get("found"))

    def test_superuser_no_active_tenant_resolves_global(self):
        """Superuser without an active tenant set in session can still resolve scanned assets."""
        self.client.force_login(self.tenant_admin)
        # Deliberately omit session['active_tenant_id'] so TenantMiddleware finds no tenant.
        resp = self.client.get(self.url, {"code": "SCAN-001"})
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["found"])


    def test_member_without_view_asset_gets_403(self):
        """Member with no assets.view_asset permission is denied."""
        # tenant_user has empty permissions (setup_tenant_context default).
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        resp = self.client.get(self.url, {"code": "SCAN-001"})
        self.assertEqual(resp.status_code, 403)
        data = json.loads(resp.content)
        self.assertFalse(data.get("found"))

    def test_member_with_view_asset_sees_own_tenant_not_other(self):
        """Member with view_asset resolves own-tenant asset and gets 404 for other tenant's tag."""
        from assets.models import Manufacturer, AssetRole, StatusLabel, AssetType

        # Give tenant_user the view_asset permission.
        self.tenant_role.permissions = ["assets.view_asset"]
        self.tenant_role.save()

        # Reuse the type / role / status already created in setUp (avoid unique-constraint clash).
        existing_atype = AssetType.objects.filter(slug="test-model").first()
        existing_role = AssetRole.objects.filter(slug="testrole").first()
        existing_status = StatusLabel.objects.filter(slug="active-scan-test").first()

        # Create an asset in a second tenant.
        other_tenant = Tenant.objects.create(name="OtherTenant", slug="other-tenant-scan")
        other_asset = Asset.objects.create(
            name="Other Tenant Asset",
            asset_tag="OTHER-999",
            serial_number="SN-OTHER-999",
            asset_type=existing_atype,
            asset_role=existing_role,
            status=existing_status,
            tenant=other_tenant,
        )

        self.client_login_to_tenant(self.tenant_user, self.tenant)

        # Own-tenant asset — should be found.
        resp = self.client.get(self.url, {"code": "SCAN-001"})
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["found"])

        # Other tenant's asset — must NOT be visible.
        resp2 = self.client.get(self.url, {"code": "OTHER-999"})
        self.assertIn(resp2.status_code, (404, 403))
        data2 = json.loads(resp2.content)
        self.assertFalse(data2.get("found"))
