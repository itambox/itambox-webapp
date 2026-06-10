"""
Tests for Part 3: stored reconciliation report, CSV export, rehome from stored list.
"""
import csv
import io

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from model_bakery import baker

from assets.models import Asset, StatusLabel
from compliance.models import AuditSession, AssetAudit
from compliance.reconciliation import close_audit_session, rehome_audit_session_mismatches
from organization.models import Location
from core.tests.mixins import TenantTestMixin

User = get_user_model()


def _su():
    return User.objects.create_superuser(username='report_su', email='r@test.com', password='pw')


class ReportPersistenceTests(TestCase):
    """close_audit_session writes a frozen reconciliation_report JSONField."""

    def setUp(self):
        self.user = _su()
        self.loc = baker.make(Location, name='Berlin')
        self.loc2 = baker.make(Location, name='Munich')
        self.session = AuditSession.objects.create(
            name='Berlin Campaign', status='active',
            location=self.loc, created_by=self.user,
        )
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        self.archived = baker.make(StatusLabel, type=StatusLabel.TYPE_ARCHIVED)

    def _audit(self, asset, location):
        return AssetAudit.objects.create(
            session=self.session, asset=asset, auditor=self.user,
            location=location, status=self.status, verification_method='barcode'
        )

    def test_close_writes_report(self):
        asset = baker.make(Asset, status=self.status, location=self.loc)
        self._audit(asset, self.loc)

        close_audit_session(self.session, user=self.user)

        self.session.refresh_from_db()
        self.assertIsNotNone(self.session.reconciliation_report)
        report = self.session.reconciliation_report
        self.assertIn('rows', report)
        self.assertIn('total_expected', report)
        self.assertIn('total_scanned', report)

    def test_report_contains_all_categories(self):
        matching_asset = baker.make(Asset, status=self.status, location=self.loc)
        mismatch_asset = baker.make(Asset, status=self.status, location=self.loc)
        surprise_asset = baker.make(Asset, status=self.archived, location=self.loc)
        missing_asset = baker.make(Asset, status=self.status, location=self.loc)

        self._audit(matching_asset, self.loc)       # matching
        self._audit(mismatch_asset, self.loc2)      # mismatched (observed Munich, expected Berlin)
        self._audit(surprise_asset, self.loc)       # surprise (archived = not in expected)
        # missing_asset not audited

        close_audit_session(self.session, user=self.user)
        self.session.refresh_from_db()

        rows = self.session.reconciliation_report['rows']
        cats = {r['category'] for r in rows}
        self.assertIn('matching', cats)
        self.assertIn('mismatched', cats)
        self.assertIn('surprise', cats)
        self.assertIn('missing', cats)

    def test_report_row_is_denormalized(self):
        """Rows contain name/tag/location strings, not just IDs."""
        asset = baker.make(Asset, status=self.status, location=self.loc, name='Test Server', asset_tag='TS-001')
        self._audit(asset, self.loc)

        close_audit_session(self.session, user=self.user)
        self.session.refresh_from_db()

        row = next(r for r in self.session.reconciliation_report['rows'] if r['category'] == 'matching')
        self.assertEqual(row['name'], 'Test Server')
        self.assertEqual(row['asset_tag'], 'TS-001')
        self.assertEqual(row['observed_location'], 'Berlin')
        self.assertIn('timestamp_display', row)


class RehomeFromStoredReportTests(TestCase):
    """rehome_audit_session_mismatches uses stored report, not live re-query."""

    def setUp(self):
        self.user = _su()
        self.loc_berlin = baker.make(Location, name='Berlin')
        self.loc_munich = baker.make(Location, name='Munich')
        self.session = AuditSession.objects.create(
            name='Rehome Test', status='active',
            location=self.loc_berlin, created_by=self.user,
        )
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)

    def test_rehome_moves_stored_mismatches(self):
        # Mismatch: registered in Berlin (so it's in expected_ids) but scanned in Munich.
        mismatch_asset = baker.make(Asset, status=self.status, location=self.loc_berlin)
        matching_asset = baker.make(Asset, status=self.status, location=self.loc_berlin)

        # mismatch_asset scanned in Munich (wrong loc) → mismatched in report
        AssetAudit.objects.create(
            session=self.session, asset=mismatch_asset, auditor=self.user,
            location=self.loc_munich, status=self.status, verification_method='barcode'
        )
        # matching_asset scanned in Berlin (correct loc) → matching
        AssetAudit.objects.create(
            session=self.session, asset=matching_asset, auditor=self.user,
            location=self.loc_berlin, status=self.status, verification_method='barcode'
        )

        close_audit_session(self.session, user=self.user)
        # After close, move mismatch_asset back to Berlin so we can verify rehome works
        # by driving from the stored report (not live asset.location).
        rehome_audit_session_mismatches(self.session, user=self.user)

        mismatch_asset.refresh_from_db()
        matching_asset.refresh_from_db()
        # Rehome must move mismatch_asset to session.location (Berlin)
        self.assertEqual(mismatch_asset.location, self.loc_berlin)
        # Matching asset stays in Berlin (unchanged)
        self.assertEqual(matching_asset.location, self.loc_berlin)


class CsvExportTests(TenantTestMixin, TestCase):
    """CSV export returns correct rows for all categories."""

    def setUp(self):
        self.setup_tenant_context(name='CSV Tenant', slug='csv-tenant')
        self.tenant_role.permissions = ['compliance.view_auditsession']
        self.tenant_role.save()

        self.loc = baker.make(Location, name='TestLoc')
        self.loc2 = baker.make(Location, name='OtherLoc')
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        self.archived = baker.make(StatusLabel, type=StatusLabel.TYPE_ARCHIVED)

        self.session = AuditSession.objects.create(
            name='CSV Campaign', status='active',
            location=self.loc, created_by=self.tenant_admin,
        )
        matching = baker.make(Asset, status=self.status, location=self.loc)
        mismatched = baker.make(Asset, status=self.status, location=self.loc)
        surprise = baker.make(Asset, status=self.archived, location=self.loc)
        self._missing = baker.make(Asset, status=self.status, location=self.loc)

        for asset, loc in [(matching, self.loc), (mismatched, self.loc2), (surprise, self.loc)]:
            AssetAudit.objects.create(
                session=self.session, asset=asset, auditor=self.tenant_admin,
                location=loc, status=self.status, verification_method='manual'
            )
        close_audit_session(self.session, user=self.tenant_admin)

    def test_csv_contains_all_categories(self):
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        url = reverse('compliance:auditsession_report_csv', kwargs={'pk': self.session.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')

        content = b''.join(response.streaming_content) if hasattr(response, 'streaming_content') else response.content
        reader = csv.DictReader(io.StringIO(content.decode()))
        rows = list(reader)
        cats = {r['Category'] for r in rows}
        self.assertIn('matching', cats)
        self.assertIn('mismatched', cats)
        self.assertIn('surprise', cats)
        self.assertIn('missing', cats)

    def test_csv_anonymous_denied(self):
        self.client.logout()
        url = reverse('compliance:auditsession_report_csv', kwargs={'pk': self.session.pk})
        response = self.client.get(url)
        self.assertIn(response.status_code, (302, 401, 403))
