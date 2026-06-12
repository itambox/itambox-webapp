"""
Tests for Part 7: AuditSession tenant scoping + Planned status.
"""
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from model_bakery import baker

from compliance.models import AuditSession, AssetAudit
from compliance.reconciliation import classify_session_audits
from assets.models import Asset, StatusLabel, AssetType, Manufacturer
from organization.models import Location, Tenant
from core.tests.mixins import TenantTestMixin

User = get_user_model()


class AuditSessionTenantScopingTests(TenantTestMixin, TestCase):
    """Tenant-scoped visibility: sessions scoped to tenant A are invisible to tenant B."""

    def setUp(self):
        self.setup_tenant_context(name='TenantA', slug='tenant-a')
        self.tenant_a = self.tenant
        self.user_a = self.tenant_user
        self.tenant_role.permissions = [
            'compliance.view_auditsession',
            'compliance.add_assetaudit',
        ]
        self.tenant_role.save()

        # Create a second tenant + user
        self.tenant_b = baker.make(Tenant, name='TenantB', slug='tenant-b')

    def _make_session(self, tenant=None, status='active'):
        return AuditSession.objects.create(
            name='Test Session',
            status=status,
            tenant=tenant,
            created_by=self.tenant_admin,
        )

    def test_session_scoped_to_tenant_a_visible_in_tenant_a_context(self):
        session = self._make_session(tenant=self.tenant_a)
        self.set_active_tenant(self.tenant_a)
        self.assertIn(session, AuditSession.objects.all())

    def test_session_scoped_to_tenant_a_invisible_in_tenant_b_context(self):
        session = self._make_session(tenant=self.tenant_a)
        self.set_active_tenant(self.tenant_b)
        self.assertNotIn(session, AuditSession.objects.all())

    def test_global_session_visible_in_any_tenant_context(self):
        """Session with tenant=None is visible from any tenant context."""
        global_session = self._make_session(tenant=None)
        self.set_active_tenant(self.tenant_a)
        self.assertIn(global_session, AuditSession.objects.all())
        self.set_active_tenant(self.tenant_b)
        self.assertIn(global_session, AuditSession.objects.all())

    def test_all_objects_finds_deleted_records_within_tenant(self):
        """all_objects includes soft-deleted records within the active tenant scope."""
        session = self._make_session(tenant=self.tenant_a)
        session.deleted_at = __import__('django.utils.timezone', fromlist=['now']).now()
        session.save(update_fields=['deleted_at'])
        self.set_active_tenant(self.tenant_a)
        # all_objects (no soft-delete filter) should find it within the tenant
        self.assertIn(session, AuditSession.all_objects.all())


class ExpectedAssetsCountStabilityTests(TenantTestMixin, TestCase):
    """expected_assets_queryset returns the same count regardless of which user calls it."""

    def setUp(self):
        self.setup_tenant_context(name='StableCountTenant', slug='stc')
        self.tenant_b = baker.make(Tenant, name='OtherTenant', slug='other-tenant')

        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        mfr = baker.make(Manufacturer)
        at = baker.make(AssetType, manufacturer=mfr)

        # 3 assets belonging to our tenant
        for _ in range(3):
            baker.make(Asset, status=self.status, tenant=self.tenant)

        # 1 asset belonging to another tenant (should NOT be counted)
        baker.make(Asset, status=self.status, tenant=self.tenant_b)

        self.session = AuditSession.objects.create(
            name='Count Test',
            status='active',
            tenant=self.tenant,
            created_by=self.tenant_admin,
        )

    def test_expected_count_same_from_both_tenant_contexts(self):
        """Count must be stable regardless of which tenant context is active."""
        self.set_active_tenant(self.tenant)
        count_a = self.session.expected_assets_queryset.count()

        self.set_active_tenant(self.tenant_b)
        count_b = self.session.expected_assets_queryset.count()

        self.assertEqual(count_a, count_b)
        self.assertEqual(count_a, 3)

    def test_global_session_counts_all_tenants(self):
        global_session = AuditSession.objects.create(
            name='Global Count',
            status='active',
            tenant=None,
            created_by=self.tenant_admin,
        )
        # Global session has no tenant filter → counts assets from all tenants
        all_count = global_session.expected_assets_queryset.count()
        self.assertGreaterEqual(all_count, 4)  # 3 + 1 at minimum

    def test_classification_stable_across_tenant_contexts(self):
        """Missing/matching classification must be identical regardless of viewer tenant."""
        loc = baker.make(Location, tenant=self.tenant)
        scanned_status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        session = AuditSession.objects.create(
            name='Classification Stability',
            status='active',
            tenant=self.tenant,
            location=loc,
            created_by=self.tenant_admin,
        )
        # 2 assets at the location — one will be scanned, one missing
        asset_scanned = baker.make(Asset, status=scanned_status, tenant=self.tenant, location=loc)
        _asset_missing = baker.make(Asset, status=scanned_status, tenant=self.tenant, location=loc)

        auditor = baker.make(User)
        AssetAudit.objects.create(
            session=session, asset=asset_scanned, auditor=auditor,
            location=loc, status=scanned_status,
        )

        self.set_active_tenant(self.tenant)
        result_a = classify_session_audits(session)
        missing_ids_a = set(result_a['missing'].values_list('pk', flat=True))

        self.set_active_tenant(self.tenant_b)
        result_b = classify_session_audits(session)
        missing_ids_b = set(result_b['missing'].values_list('pk', flat=True))

        self.assertEqual(missing_ids_a, missing_ids_b,
                         "Missing asset set must be identical regardless of viewer tenant")


class PlannedSessionTests(TenantTestMixin, TestCase):
    """Planned sessions reject scans; start action activates them."""

    def setUp(self):
        self.setup_tenant_context(name='PlannedTenant', slug='planned-tenant')
        self.tenant_role.permissions = [
            'compliance.add_assetaudit',
            'compliance.change_auditsession',
        ]
        self.tenant_role.save()

        self.session = AuditSession.objects.create(
            name='Planned Session',
            status='planned',
            tenant=self.tenant,
            created_by=self.tenant_admin,
        )
        self.scan_url = reverse('compliance:auditsession_scan', kwargs={'pk': self.session.pk})
        self.start_url = reverse('compliance:auditsession_start', kwargs={'pk': self.session.pk})

    def test_scan_on_planned_session_returns_404(self):
        """Scan view uses get_object_or_404(..., status='active') — planned → 404."""
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        response = self.client.post(
            self.scan_url, data={'barcode': 'ITM-00001'},
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(response.status_code, 404)

    def test_start_action_transitions_to_active(self):
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        response = self.client.post(self.start_url, HTTP_HX_REQUEST='true')
        self.assertIn(response.status_code, (200, 204))
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'active')

    def test_anonymous_start_denied(self):
        self.client.logout()
        response = self.client.post(self.start_url)
        self.assertIn(response.status_code, (302, 401, 403))

    def test_create_with_start_immediately_false_stays_planned(self):
        """Creating with start_immediately=False leaves session in planned state."""
        self.client_login_to_tenant(self.tenant_admin, self.tenant)
        response = self.client.post(
            reverse('compliance:auditsession_create'),
            data={
                'name': 'Future Campaign',
                'start_immediately': '',  # unchecked checkbox = empty string
            },
        )
        # Should redirect to the detail page on success
        self.assertIn(response.status_code, (200, 302))
        session = AuditSession.objects.filter(name='Future Campaign').first()
        self.assertIsNotNone(session)
        self.assertEqual(session.status, 'planned')

    def test_create_with_start_immediately_true_starts_active(self):
        self.client_login_to_tenant(self.tenant_admin, self.tenant)
        response = self.client.post(
            reverse('compliance:auditsession_create'),
            data={
                'name': 'Immediate Campaign',
                'start_immediately': 'on',
            },
        )
        self.assertIn(response.status_code, (200, 302))
        session = AuditSession.objects.filter(name='Immediate Campaign').first()
        self.assertIsNotNone(session)
        self.assertEqual(session.status, 'active')


class AuditSessionPermissionTests(TenantTestMixin, TestCase):
    """Close and rehome views must reject zero-permission members."""

    def setUp(self):
        self.setup_tenant_context(name='PermTenant', slug='perm-tenant')
        # Zero-permission role: no permissions at all
        self.tenant_role.permissions = []
        self.tenant_role.save()

        loc = baker.make(Location, tenant=self.tenant)
        self.active_session = AuditSession.objects.create(
            name='Active Session',
            status='active',
            tenant=self.tenant,
            location=loc,
            created_by=self.tenant_admin,
        )
        self.completed_session = AuditSession.objects.create(
            name='Completed Session',
            status='completed',
            tenant=self.tenant,
            location=loc,
            created_by=self.tenant_admin,
        )

    def test_zero_perm_member_cannot_close_session(self):
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        url = reverse('compliance:auditsession_close', kwargs={'pk': self.active_session.pk})
        response = self.client.post(url, HTTP_HX_REQUEST='true')
        self.assertIn(response.status_code, (302, 403))
        self.active_session.refresh_from_db()
        self.assertEqual(self.active_session.status, 'active')

    def test_zero_perm_member_cannot_rehome_session(self):
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        url = reverse('compliance:auditsession_rehome', kwargs={'pk': self.completed_session.pk})
        response = self.client.post(url, HTTP_HX_REQUEST='true')
        self.assertIn(response.status_code, (302, 403))
