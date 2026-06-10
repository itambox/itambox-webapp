"""
Permission tests for both audit endpoints.

Covers:
  - AssetAuditScanView (compliance:auditsession_scan): anonymous, authed-no-perm, authed-with-perm
  - AssetAuditView (assets:asset_audit): anonymous, authed-no-perm, authed-with-perm

Both endpoints must require compliance.add_assetaudit.
"""
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from model_bakery import baker
from organization.models import Location
from assets.models import Asset, StatusLabel
from compliance.models import AuditSession, AssetAudit
from core.tests.mixins import TenantTestMixin

User = get_user_model()

AUDIT_PERM = 'compliance.add_assetaudit'


class AuditScanViewPermissionTests(TenantTestMixin, TestCase):
    """compliance:auditsession_scan — HTMX barcode-scan endpoint."""

    def setUp(self):
        self.setup_tenant_context(name='Perm Tenant', slug='perm-tenant')
        loc = baker.make(Location, name='Warehouse')
        self.session = AuditSession.objects.create(
            name='Test Campaign',
            status='active',
            location=loc,
            created_by=self.tenant_admin,
        )
        self.url = reverse('compliance:auditsession_scan', kwargs={'pk': self.session.pk})
        self.htmx_headers = {'HTTP_HX_REQUEST': 'true'}

    def _post(self, user=None, **extra):
        if user:
            self.client.force_login(user)
        else:
            self.client.logout()
        return self.client.post(self.url, {'barcode': 'DUMMY'}, **extra)

    def test_anonymous_redirects_to_login(self):
        response = self._post()
        self.assertIn(response.status_code, (302, 401))

    def test_authed_no_perm_denied(self):
        # tenant_user has no permissions (setup_tenant_context passes empty list)
        response = self._post(user=self.tenant_user)
        self.assertIn(response.status_code, (302, 403))
        self.assertEqual(AssetAudit.objects.filter(session=self.session).count(), 0)

    def test_authed_no_perm_htmx_returns_204_danger(self):
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        response = self.client.post(self.url, {'barcode': 'DUMMY'}, **self.htmx_headers)
        self.assertEqual(response.status_code, 204)
        self.assertIn('showMessage', response.get('HX-Trigger', ''))
        self.assertIn('danger', response.get('HX-Trigger', ''))
        self.assertEqual(AssetAudit.objects.filter(session=self.session).count(), 0)

    def test_authed_with_perm_processes_scan(self):
        self.tenant_role.permissions = [AUDIT_PERM, 'assets.view_asset']
        self.tenant_role.save()
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        asset = baker.make(
            Asset,
            asset_tag='SCAN-001',
            tenant=self.tenant,
            location=self.session.location,
        )
        response = self.client.post(
            self.url, {'barcode': asset.asset_tag}, **self.htmx_headers
        )
        # Should succeed (200 rendered partial) or at least not 302/403
        self.assertNotIn(response.status_code, (302, 403))


class AssetAuditViewPermissionTests(TenantTestMixin, TestCase):
    """assets:asset_audit — standalone per-asset audit POST."""

    def setUp(self):
        self.setup_tenant_context(name='AssetAudit Perm Tenant', slug='asset-audit-perm-tenant')
        loc = baker.make(Location, name='ServerRoom')
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        self.asset = baker.make(Asset, asset_tag='ASSET-001', tenant=self.tenant, location=loc, status=self.status)
        self.url = reverse('assets:asset_audit', kwargs={'pk': self.asset.pk})

    def _post(self, user=None):
        if user:
            self.client.force_login(user)
        else:
            self.client.logout()
        return self.client.post(self.url)

    def test_anonymous_redirects_to_login(self):
        response = self._post()
        self.assertIn(response.status_code, (302, 401))

    def test_authed_no_perm_denied(self):
        response = self._post(user=self.tenant_user)
        self.assertIn(response.status_code, (302, 403))
        self.assertEqual(AssetAudit.objects.filter(asset=self.asset).count(), 0)

    def test_authed_with_perm_creates_audit_row(self):
        # Grant permissions on the existing role (setup_tenant_context created it with empty perms)
        self.tenant_role.permissions = [AUDIT_PERM, 'assets.view_asset']
        self.tenant_role.save()
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        response = self.client.post(self.url)
        # SimplePostView redirects on success (non-HTMX)
        self.assertIn(response.status_code, (200, 204, 302))
        self.assertEqual(AssetAudit.objects.filter(asset=self.asset).count(), 1)
