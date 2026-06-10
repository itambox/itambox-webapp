"""
Tests for classify_session_audits correctness (Part 2).

Covers:
- Global session: all scanned assets are "matching" regardless of their location.
- Located session: classification uses the AUDIT record's observed location_id,
  not the asset's current live location (mismatch is frozen at scan time).
- Surprise finds: assets scanned that are not in expected_ids.
- Missing: expected assets not scanned.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from model_bakery import baker

from assets.models import Asset, StatusLabel
from compliance.models import AuditSession, AssetAudit
from compliance.reconciliation import classify_session_audits, audit_asset
from organization.models import Location

User = get_user_model()


def _superuser():
    u = User.objects.create_superuser(username='auditor_su', email='a@test.com', password='pw')
    return u


class ClassifyGlobalSessionTests(TestCase):
    """Global session (no location): all scanned assets are matching."""

    def setUp(self):
        self.user = _superuser()
        self.session = AuditSession.objects.create(
            name='Global Stocktake', status='active', location=None, created_by=self.user
        )
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        self.loc_a = baker.make(Location, name='Berlin')
        self.loc_b = baker.make(Location, name='Munich')

    def _audit(self, asset, location):
        return AssetAudit.objects.create(
            session=self.session, asset=asset, auditor=self.user,
            location=location, status=self.status, verification_method='manual'
        )

    def test_located_assets_are_matching_in_global_session(self):
        asset_a = baker.make(Asset, status=self.status, location=self.loc_a)
        asset_b = baker.make(Asset, status=self.status, location=self.loc_b)
        self._audit(asset_a, self.loc_a)
        self._audit(asset_b, self.loc_b)

        result = classify_session_audits(self.session)

        self.assertEqual(len(result['matching']), 2)
        self.assertEqual(len(result['mismatched']), 0)
        self.assertEqual(len(result['surprise']), 0)

    def test_missing_not_scanned(self):
        expected_asset = baker.make(
            Asset, status=self.status, location=self.loc_a,
        )
        # Don't scan it — it should appear in missing
        result = classify_session_audits(self.session)
        missing_ids = set(result['missing'].values_list('id', flat=True))
        self.assertIn(expected_asset.id, missing_ids)


class ClassifyLocatedSessionTests(TestCase):
    """Located session: mismatch based on audit.location_id, not asset.location."""

    def setUp(self):
        self.user = _superuser()
        self.loc_berlin = baker.make(Location, name='Berlin')
        self.loc_munich = baker.make(Location, name='Munich')
        self.session = AuditSession.objects.create(
            name='Berlin Campaign', status='active',
            location=self.loc_berlin, created_by=self.user
        )
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)

    def _audit(self, asset, observed_location):
        return AssetAudit.objects.create(
            session=self.session, asset=asset, auditor=self.user,
            location=observed_location, status=self.status, verification_method='barcode'
        )

    def test_matching_when_audit_location_equals_session_location(self):
        """Asset in Berlin, scanned in Berlin: classified matching.
        Comparison uses audit.location_id (immutable), not asset.location."""
        asset = baker.make(Asset, status=self.status, location=self.loc_berlin)
        self._audit(asset, self.loc_berlin)

        result = classify_session_audits(self.session)
        self.assertEqual(len(result['matching']), 1)
        self.assertEqual(len(result['mismatched']), 0)
        self.assertEqual(len(result['surprise']), 0)

    def test_mismatch_uses_audit_location_not_live_asset_location(self):
        """Asset was scanned in Munich (wrong location for Berlin campaign). Even if
        someone later moves asset.location to Berlin, mismatch must persist."""
        asset = baker.make(Asset, status=self.status, location=self.loc_munich)
        self._audit(asset, self.loc_munich)

        # Move the live asset back to Berlin AFTER the scan
        asset.location = self.loc_berlin
        asset.save(update_fields=['location'])

        result = classify_session_audits(self.session)
        self.assertEqual(len(result['mismatched']), 1)
        self.assertEqual(len(result['matching']), 0)

    def test_surprise_find_not_in_expected(self):
        """An archived asset (excluded from expected) gets scanned — surprise."""
        archived_status = baker.make(StatusLabel, type=StatusLabel.TYPE_ARCHIVED)
        surprise_asset = baker.make(Asset, status=archived_status, location=self.loc_berlin)
        self._audit(surprise_asset, self.loc_berlin)

        result = classify_session_audits(self.session)
        surprise_ids = {a.asset_id for a in result['surprise']}
        self.assertIn(surprise_asset.id, surprise_ids)
        self.assertEqual(len(result['matching']), 0)
        self.assertEqual(len(result['mismatched']), 0)

    def test_missing_expected_not_scanned(self):
        expected = baker.make(Asset, status=self.status, location=self.loc_berlin)
        # No audit created for expected
        result = classify_session_audits(self.session)
        missing_ids = set(result['missing'].values_list('id', flat=True))
        self.assertIn(expected.id, missing_ids)
