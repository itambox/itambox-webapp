"""
Tests for Part 5: Flag-missing-as-Missing bulk action.
"""
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from model_bakery import baker

from assets.models import Asset, StatusLabel
from compliance.models import AuditSession, AssetAudit
from compliance.reconciliation import close_audit_session, flag_missing_assets
from organization.models import Location
from core.tests.mixins import TenantTestMixin

User = get_user_model()


def _su():
    return User.objects.create_superuser(
        username='flag_su', email='flag@test.com', password='pw'
    )


class FlagMissingServiceTests(TestCase):
    """flag_missing_assets() service unit tests."""

    def setUp(self):
        self.user = _su()
        self.loc = baker.make(Location, name='Warehouse')
        self.deployable = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        self.session = AuditSession.objects.create(
            name='Flag Test', status='active',
            location=self.loc, created_by=self.user,
        )

    def _close_with_missing(self, missing_count=1):
        assets = [
            baker.make(Asset, status=self.deployable, location=self.loc)
            for _ in range(missing_count)
        ]
        close_audit_session(self.session, user=self.user)
        self.session.refresh_from_db()
        return assets

    def test_missing_assets_get_missing_status(self):
        assets = self._close_with_missing(2)
        result = flag_missing_assets(self.session, user=self.user)
        self.assertEqual(result['flagged'], 2)
        self.assertEqual(result['skipped'], 0)
        for asset in assets:
            asset.refresh_from_db()
            self.assertEqual(asset.status.name, 'Missing')
            self.assertEqual(asset.status.type, StatusLabel.TYPE_UNDEPLOYABLE)

    def test_already_changed_status_is_skipped(self):
        assets = self._close_with_missing(2)
        # Manually change the first asset's status before flagging
        other_status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE, name='In Repair')
        assets[0].status = other_status
        assets[0].save(update_fields=['status'])

        result = flag_missing_assets(self.session, user=self.user)
        self.assertEqual(result['flagged'], 1)
        self.assertEqual(result['skipped'], 1)

        assets[0].refresh_from_db()
        self.assertEqual(assets[0].status, other_status)  # unchanged
        assets[1].refresh_from_db()
        self.assertEqual(assets[1].status.name, 'Missing')

    def test_idempotent_second_run(self):
        """Re-running after status is already 'Missing' still works (status unchanged)."""
        assets = self._close_with_missing(1)
        flag_missing_assets(self.session, user=self.user)

        # Second run: asset status is now 'Missing', which differs from the
        # original stored status_id (deployable) → skipped.
        result = flag_missing_assets(self.session, user=self.user)
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['flagged'], 0)

    def test_raises_if_session_not_closed(self):
        from django.core.exceptions import ValidationError
        self.session.status = 'active'
        self.session.save()
        with self.assertRaises(ValidationError):
            flag_missing_assets(self.session, user=self.user)

    def test_no_missing_rows_returns_zero(self):
        asset = baker.make(Asset, status=self.deployable, location=self.loc)
        AssetAudit.objects.create(
            session=self.session, asset=asset, auditor=self.user,
            location=self.loc, status=self.deployable, verification_method='manual',
        )
        close_audit_session(self.session, user=self.user)
        self.session.refresh_from_db()

        result = flag_missing_assets(self.session, user=self.user)
        self.assertEqual(result['flagged'], 0)
        self.assertEqual(result['skipped'], 0)

    def test_missing_status_is_get_or_create(self):
        """StatusLabel 'Missing' is reused if it already exists."""
        existing = StatusLabel.objects.create(
            name='Missing', type=StatusLabel.TYPE_UNDEPLOYABLE, color='#000'
        )
        self._close_with_missing(1)
        flag_missing_assets(self.session, user=self.user)
        # Should not create a second 'Missing' label
        self.assertEqual(StatusLabel.objects.filter(name='Missing').count(), 1)


class FlagMissingViewTests(TenantTestMixin, TestCase):
    """AuditSessionFlagMissingView: GET modal; POST executes."""

    def setUp(self):
        self.setup_tenant_context(name='FlagTenant', slug='flag-tenant')
        self.tenant_role.permissions = ['compliance.change_asset', 'compliance.view_auditsession']
        self.tenant_role.save()

        self.loc = baker.make(Location, name='FlagRoom', tenant=self.tenant)
        self.deployable = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        self.session = AuditSession.objects.create(
            name='Flag View Test', status='active',
            location=self.loc, created_by=self.tenant_admin,
        )
        self.missing_asset = baker.make(Asset, status=self.deployable, location=self.loc, tenant=self.tenant)
        close_audit_session(self.session, user=self.tenant_admin)
        self.session.refresh_from_db()
        self.url = reverse('compliance:auditsession_flag_missing', kwargs={'pk': self.session.pk})

    def test_get_renders_modal_with_count(self):
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'flag-missing-modal')
        self.assertContains(response, '1')  # missing_count

    def test_post_executes_flag_action(self):
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        response = self.client.post(
            self.url, data={}, HTTP_HX_REQUEST='true',
        )
        self.assertIn(response.status_code, (200, 204))
        self.missing_asset.refresh_from_db()
        self.assertEqual(self.missing_asset.status.name, 'Missing')


    def test_anonymous_denied(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (302, 401, 403))
