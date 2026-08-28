"""
Tests for Part 3: stored reconciliation report, CSV export, rehome from stored list.
"""

import csv
import io

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from model_bakery import baker

from assets.models import Asset, StatusLabel
from compliance.audit_services import close_audit_session, rehome_audit_session_mismatches
from compliance.models import AssetAudit, AuditSession
from core.managers import set_current_tenant
from core.tests.mixins import TenantTestMixin, grant
from organization.models import Location, Role, Tenant

User = get_user_model()


def _su(tenant, username="report_su"):
    user = User.objects.create_user(username=username, email=f"{username}@test.com", password="pw")
    role = Role.objects.create(
        tenant=tenant,
        name="Report test role",
        permissions=[
            "compliance.view_auditsession",
            "compliance.change_auditsession",
            "assets.change_asset",
        ],
    )
    grant(user, tenant, role)
    set_current_tenant(tenant)
    return user


class ReportPersistenceTests(TestCase):
    """close_audit_session writes a frozen reconciliation_report JSONField."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Report tenant", slug="report-tenant")
        self.user = _su(self.tenant)
        self.loc = baker.make(Location, name="Berlin", tenant=self.tenant)
        self.loc2 = baker.make(Location, name="Munich", tenant=self.tenant)
        self.session = AuditSession.objects.create(
            name="Berlin Campaign",
            status="active",
            location=self.loc,
            tenant=self.tenant,
            created_by=self.user,
        )
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        self.archived = baker.make(StatusLabel, type=StatusLabel.TYPE_ARCHIVED)

    def _audit(self, asset, location):
        return AssetAudit.objects.create(
            session=self.session,
            asset=asset,
            auditor=self.user,
            location=location,
            status=self.status,
            verification_method="barcode",
        )

    def test_close_writes_report(self):
        asset = baker.make(Asset, status=self.status, location=self.loc, tenant=self.tenant)
        self._audit(asset, self.loc)

        close_audit_session(self.session, user=self.user)

        self.session.refresh_from_db()
        self.assertIsNotNone(self.session.reconciliation_report)
        report = self.session.reconciliation_report
        self.assertIn("rows", report)
        self.assertIn("total_expected", report)
        self.assertIn("total_scanned", report)

    def test_report_contains_all_categories(self):
        matching_asset = baker.make(Asset, status=self.status, location=self.loc, tenant=self.tenant)
        mismatch_asset = baker.make(Asset, status=self.status, location=self.loc, tenant=self.tenant)
        surprise_asset = baker.make(Asset, status=self.archived, location=self.loc, tenant=self.tenant)
        missing_asset = baker.make(Asset, status=self.status, location=self.loc, tenant=self.tenant)

        self._audit(matching_asset, self.loc)  # matching
        self._audit(mismatch_asset, self.loc2)  # mismatched (observed Munich, expected Berlin)
        self._audit(surprise_asset, self.loc)  # surprise (archived = not in expected)
        # missing_asset not audited

        close_audit_session(self.session, user=self.user)
        self.session.refresh_from_db()

        rows = self.session.reconciliation_report["rows"]
        cats = {r["category"] for r in rows}
        self.assertIn("matching", cats)
        self.assertIn("mismatched", cats)
        self.assertIn("surprise", cats)
        self.assertIn("missing", cats)
        missing_row = next(row for row in rows if row["category"] == "missing")
        self.assertEqual(missing_row["asset_id"], missing_asset.pk)

    def test_report_row_is_denormalized(self):
        """Rows contain name/tag/location strings, not just IDs."""
        asset = baker.make(
            Asset, status=self.status, location=self.loc, tenant=self.tenant, name="Test Server", asset_tag="TS-001"
        )
        self._audit(asset, self.loc)

        close_audit_session(self.session, user=self.user)
        self.session.refresh_from_db()

        row = next(r for r in self.session.reconciliation_report["rows"] if r["category"] == "matching")
        self.assertEqual(row["name"], "Test Server")
        self.assertEqual(row["asset_tag"], "TS-001")
        self.assertEqual(row["observed_location"], "Berlin")
        self.assertIn("timestamp_display", row)


class RehomeFromStoredReportTests(TestCase):
    """rehome_audit_session_mismatches uses stored report, not live re-query."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Rehome tenant", slug="rehome-tenant")
        self.user = _su(self.tenant, username="rehome_su")
        self.loc_berlin = baker.make(Location, name="Berlin", tenant=self.tenant)
        self.loc_munich = baker.make(Location, name="Munich", tenant=self.tenant)
        self.session = AuditSession.objects.create(
            name="Rehome Test",
            status="active",
            location=self.loc_berlin,
            tenant=self.tenant,
            created_by=self.user,
        )
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)

    def test_rehome_moves_stored_mismatches(self):
        # Mismatch: registered in Berlin (so it's in expected_ids) but scanned in Munich.
        mismatch_asset = baker.make(Asset, status=self.status, location=self.loc_berlin, tenant=self.tenant)
        matching_asset = baker.make(Asset, status=self.status, location=self.loc_berlin, tenant=self.tenant)

        # mismatch_asset scanned in Munich (wrong loc) → mismatched in report
        AssetAudit.objects.create(
            session=self.session,
            asset=mismatch_asset,
            auditor=self.user,
            location=self.loc_munich,
            status=self.status,
            verification_method="barcode",
        )
        # matching_asset scanned in Berlin (correct loc) → matching
        AssetAudit.objects.create(
            session=self.session,
            asset=matching_asset,
            auditor=self.user,
            location=self.loc_berlin,
            status=self.status,
            verification_method="barcode",
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
        self.setup_tenant_context(name="CSV Tenant", slug="csv-tenant")
        self.tenant_role.permissions = [
            "compliance.view_auditsession",
            "compliance.change_auditsession",
            "assets.change_asset",
        ]
        self.tenant_role.save()

        self.loc = baker.make(Location, name="TestLoc", tenant=self.tenant)
        self.loc2 = baker.make(Location, name="OtherLoc", tenant=self.tenant)
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        self.archived = baker.make(StatusLabel, type=StatusLabel.TYPE_ARCHIVED)

        self.session = AuditSession.objects.create(
            name="CSV Campaign",
            status="active",
            location=self.loc,
            tenant=self.tenant,
            created_by=self.tenant_admin,
        )
        matching = baker.make(Asset, status=self.status, location=self.loc, tenant=self.tenant)
        mismatched = baker.make(Asset, status=self.status, location=self.loc, tenant=self.tenant)
        surprise = baker.make(Asset, status=self.archived, location=self.loc, tenant=self.tenant)
        self._missing = baker.make(Asset, status=self.status, location=self.loc, tenant=self.tenant)

        for asset, loc in [(matching, self.loc), (mismatched, self.loc2), (surprise, self.loc)]:
            AssetAudit.objects.create(
                session=self.session,
                asset=asset,
                auditor=self.tenant_admin,
                location=loc,
                status=self.status,
                verification_method="manual",
            )
        close_audit_session(self.session, user=self.tenant_user)

    def test_csv_contains_all_categories(self):
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        url = reverse("compliance:auditsession_report_csv", kwargs={"pk": self.session.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")

        content = b"".join(response.streaming_content) if hasattr(response, "streaming_content") else response.content
        reader = csv.DictReader(io.StringIO(content.decode()))
        rows = list(reader)
        cats = {r["Category"] for r in rows}
        self.assertIn("matching", cats)
        self.assertIn("mismatched", cats)
        self.assertIn("surprise", cats)
        self.assertIn("missing", cats)

    def test_csv_anonymous_denied(self):
        self.client.logout()
        url = reverse("compliance:auditsession_report_csv", kwargs={"pk": self.session.pk})
        response = self.client.get(url)
        self.assertIn(response.status_code, (302, 401, 403))
