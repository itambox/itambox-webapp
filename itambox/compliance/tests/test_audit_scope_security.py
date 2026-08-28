"""Adversarial actor/system and frozen-report tests for issue #446."""

import copy
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker

from assets.models import Asset, StatusLabel
from compliance.audit_services import (
    close_audit_session,
    expected_assets_queryset,
    read_reconciliation_report,
    rehome_audit_session_mismatches,
)
from compliance.models import AssetAudit, AuditSession
from core.context import set_current_tenant
from core.tasks.context import TaskContext
from core.tests.mixins import TenantTestMixin, grant
from organization.models import Location, Role, Tenant


class AuditScopeSecurityTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Audit A", slug="audit-a")
        self.tenant_b = Tenant.objects.create(name="Audit B", slug="audit-b")
        self.user = baker.make("users.User", username="audit-owner", is_active=True)
        self.role = Role.objects.create(
            tenant=self.tenant_a,
            name="Arbitrary audit role",
            permissions=[
                "compliance.view_auditsession",
                "compliance.add_assetaudit",
                "compliance.change_auditsession",
                "assets.change_asset",
            ],
        )
        self.grant = grant(self.user, self.tenant_a, self.role)
        self.set_active_tenant(self.tenant_a, self.grant.membership)
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        self.location_a = baker.make(Location, tenant=self.tenant_a, name="Audit A room")
        self.location_b = baker.make(Location, tenant=self.tenant_b, name="Audit B room")
        self.asset_a = baker.make(
            Asset,
            tenant=self.tenant_a,
            location=self.location_a,
            status=self.status,
            name="A asset",
        )
        self.asset_b = baker.make(
            Asset,
            tenant=self.tenant_b,
            location=self.location_b,
            status=self.status,
            name="B asset",
        )
        self.session = AuditSession.objects.create(
            name="Tenant A audit",
            tenant=self.tenant_a,
            location=self.location_a,
            status="active",
            created_by=self.user,
        )

    def tearDown(self):
        set_current_tenant(None)
        super().tearDown()

    def test_expected_set_is_exact_and_not_ambient(self):
        self.assertEqual(
            set(expected_assets_queryset(self.session, user=self.user).values_list("pk", flat=True)),
            {self.asset_a.pk},
        )
        self.set_active_tenant(self.tenant_b)
        self.assertEqual(
            set(expected_assets_queryset(self.session, user=self.user).values_list("pk", flat=True)),
            {self.asset_a.pk},
        )

    def test_actorless_and_contradictory_authorization_are_denied(self):
        cases = (
            {"user": None},
            {"user": None, "system_authorization": True},
            {"user": None, "system_authorization": {"tenant_id": self.tenant_a.pk}},
            {"user": self.user, "system_authorization": object()},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(PermissionDenied):
                expected_assets_queryset(self.session, **kwargs)

    def test_valid_issued_system_authorization_is_tenant_bound(self):
        with TaskContext(tenant_id=self.tenant_a.pk, user_id=None) as task:
            authorization = task.authorize_system(
                permission="compliance.view_auditsession",
                operation="compliance.audit.expected_assets",
                reason="Audit scope security test",
            )
            result = expected_assets_queryset(
                self.session,
                user=None,
                system_authorization=authorization,
            )
            self.assertEqual(set(result.values_list("pk", flat=True)), {self.asset_a.pk})

    def test_global_system_authorization_is_rejected(self):
        global_session = AuditSession.objects.create(
            name="Global audit",
            tenant=None,
            status="active",
            created_by=self.user,
        )
        with TaskContext(tenant_id=self.tenant_a.pk, user_id=None) as task:
            authorization = task.authorize_system(
                permission="compliance.view_auditsession",
                operation="compliance.audit.expected_assets",
                reason="Global denial test",
            )
            with self.assertRaises(PermissionDenied):
                expected_assets_queryset(global_session, user=None, system_authorization=authorization)

    def test_forged_cloned_wrong_tenant_and_escaped_authorizations_are_denied(self):
        with TaskContext(tenant_id=self.tenant_a.pk, user_id=None) as task:
            authorization = task.authorize_system(
                permission="compliance.view_auditsession",
                operation="compliance.audit.expected_assets",
                reason="Forgery test",
            )
            with self.assertRaises(PermissionDenied):
                expected_assets_queryset(self.session, user=None, system_authorization=copy.copy(authorization))
            set_current_tenant(self.tenant_b)
            with self.assertRaises(PermissionDenied):
                expected_assets_queryset(self.session, user=None, system_authorization=authorization)
        with self.assertRaises(PermissionDenied):
            expected_assets_queryset(self.session, user=None, system_authorization=authorization)

    def test_wrong_operation_authorization_is_denied(self):
        with TaskContext(tenant_id=self.tenant_a.pk, user_id=None) as task:
            authorization = task.authorize_system(
                permission="compliance.view_auditsession",
                operation="compliance.audit.report.read",
                reason="Wrong operation test",
            )
            with self.assertRaises(PermissionDenied):
                expected_assets_queryset(self.session, user=None, system_authorization=authorization)

    def test_permission_revocation_changes_the_next_read(self):
        self.assertEqual(expected_assets_queryset(self.session, user=self.user).count(), 1)
        self.role.permissions = []
        self.role.save(update_fields=["permissions"])
        with self.assertRaises(PermissionDenied):
            expected_assets_queryset(self.session, user=self.user)

    def test_partial_global_close_denies_before_any_session_mutation(self):
        global_session = AuditSession.objects.create(
            name="Partial global audit",
            tenant=None,
            status="active",
            created_by=self.user,
        )
        before = (global_session.status, global_session.completed_at, global_session.reconciliation_report)
        with self.assertRaises(PermissionDenied):
            close_audit_session(global_session, user=self.user)
        global_session.refresh_from_db()
        self.assertEqual(
            (global_session.status, global_session.completed_at, global_session.reconciliation_report),
            before,
        )

    def test_tenant_bound_close_rejects_foreign_observed_asset_without_mutation(self):
        AssetAudit.objects.create(
            session=self.session,
            asset=self.asset_b,
            auditor=self.user,
            location=self.location_b,
            status=self.status,
        )
        before = (self.session.status, self.session.completed_at, self.session.reconciliation_report)
        with self.assertRaises(PermissionDenied):
            close_audit_session(self.session, user=self.user)
        self.session.refresh_from_db()
        self.assertEqual(
            (self.session.status, self.session.completed_at, self.session.reconciliation_report),
            before,
        )

    def test_v2_close_writes_positive_asset_tenant_provenance_and_derived_totals(self):
        AssetAudit.objects.create(
            session=self.session,
            asset=self.asset_a,
            auditor=self.user,
            location=self.location_a,
            status=self.status,
        )
        close_audit_session(self.session, user=self.user)
        self.session.refresh_from_db()
        report = self.session.reconciliation_report
        self.assertEqual(report["schema_version"], 2)
        self.assertTrue(report["rows"])
        self.assertTrue(all(type(row["tenant_id"]) is int and row["tenant_id"] > 0 for row in report["rows"]))
        self.assertEqual(report["total_expected"], 1)
        self.assertEqual(report["total_scanned"], 1)

    def test_unknown_and_malformed_report_versions_fail_closed(self):
        for report in (
            {"schema_version": "2", "rows": []},
            {"schema_version": 99, "rows": []},
            {"schema_version": 2, "rows": [{"category": "matching"}]},
        ):
            with self.subTest(report=report):
                self.session.reconciliation_report = report
                with self.assertRaisesMessage(
                    ValidationError,
                    "The stored reconciliation report cannot be read safely.",
                ):
                    read_reconciliation_report(self.session, user=self.user)

    def test_v1_invalid_category_fails_closed_through_all_report_consumers(self):
        malformed = {"rows": [{"category": "forged", "asset_id": self.asset_a.pk}]}
        self.session.status = "completed"
        self.session.reconciliation_report = malformed
        self.session.save(update_fields=["status", "reconciliation_report"])

        with self.assertRaisesMessage(ValidationError, "The stored reconciliation report cannot be read safely."):
            read_reconciliation_report(self.session, user=self.user)
        with self.assertRaisesMessage(ValidationError, "The stored reconciliation report cannot be read safely."):
            rehome_audit_session_mismatches(self.session, user=self.user)
        with self.assertRaisesMessage(ValidationError, "The stored reconciliation report cannot be read safely."):
            from compliance.audit_services import flag_missing_assets

            flag_missing_assets(self.session, user=self.user)

    def test_malformed_report_detail_and_csv_paths_return_handled_denial(self):
        self.session.status = "completed"
        self.session.reconciliation_report = {"rows": [{"category": "forged", "asset_id": self.asset_a.pk}]}
        self.session.save(update_fields=["status", "reconciliation_report"])
        self.client_login_to_tenant(self.user, self.tenant_a)

        for url in (
            reverse("compliance:auditsession_detail", kwargs={"pk": self.session.pk}),
            reverse("compliance:auditsession_report_csv", kwargs={"pk": self.session.pk}),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403)

    def test_commit_uses_scan_authorization_inside_transaction(self):
        scan_user = baker.make("users.User", username="scan-only", is_active=True)
        scan_role = Role.objects.create(
            tenant=self.tenant_a,
            name="Scan-only role",
            permissions=["compliance.add_assetaudit"],
        )
        scan_grant = grant(scan_user, self.tenant_a, scan_role)
        self.client_login_to_tenant(scan_user, self.tenant_a)

        response = self.client.post(
            reverse("compliance:auditsession_commit", kwargs={"pk": self.session.pk}),
            {"pk": [self.asset_a.pk]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(AssetAudit.objects.filter(session=self.session, asset=self.asset_a).exists())
        self.assertIsNotNone(scan_grant)

    def test_v1_reader_resolves_provenance_in_one_bulk_query_and_filters_rows(self):
        deleted = baker.make(Asset, tenant=self.tenant_a, location=self.location_a, status=self.status)
        deleted.delete()
        self.session.reconciliation_report = {
            "rows": [
                {"category": "matching", "asset_id": self.asset_a.pk, "tenant_id": self.tenant_b.pk},
                {"category": "matching", "asset_id": self.asset_b.pk},
                {"category": "missing", "asset_id": deleted.pk},
                {"category": "matching", "asset_id": "not-an-id"},
            ]
        }
        self.session.save(update_fields=["reconciliation_report"])
        expected_assets_queryset(self.session, user=self.user).count()
        with CaptureQueriesContext(connection) as queries:
            report = read_reconciliation_report(self.session, user=self.user)
        self.assertEqual(len(queries), 1)
        self.assertEqual([row["asset_id"] for row in report["rows"]], [self.asset_a.pk])
        self.assertEqual(report["rows"][0]["tenant_id"], self.tenant_a.pk)
        self.assertEqual((report["total_expected"], report["total_scanned"]), (1, 1))

    def test_v2_reader_has_no_provenance_query_or_n_plus_one(self):
        self.session.reconciliation_report = {
            "schema_version": 2,
            "total_expected": 1,
            "total_scanned": 1,
            "rows": [
                {
                    "tenant_id": self.tenant_a.pk,
                    "category": "matching",
                    "asset_id": self.asset_a.pk,
                }
            ],
        }
        self.session.save(update_fields=["reconciliation_report"])
        expected_assets_queryset(self.session, user=self.user).count()
        with CaptureQueriesContext(connection) as queries:
            report = read_reconciliation_report(self.session, user=self.user)
        self.assertEqual(len(queries), 0)
        self.assertEqual(report["rows"][0]["tenant_id"], self.tenant_a.pk)

    def test_mutation_re_resolves_report_asset_ids_with_tenant_filter(self):
        self.session.status = "completed"
        self.session.reconciliation_report = {
            "schema_version": 2,
            "total_expected": 0,
            "total_scanned": 1,
            "rows": [
                {
                    "tenant_id": self.tenant_a.pk,
                    "category": "mismatched",
                    "asset_id": self.asset_b.pk,
                }
            ],
        }
        self.session.save(update_fields=["status", "reconciliation_report"])
        before = self.asset_b.location_id
        rehome_audit_session_mismatches(self.session, user=self.user)
        self.asset_b.refresh_from_db()
        self.assertEqual(self.asset_b.location_id, before)

    def test_expiring_grant_is_not_a_permission_map_shortcut(self):
        grant_row = self.grant
        expiry = timezone.now() + timedelta(seconds=1)
        type(grant_row).objects.filter(pk=grant_row.pk).update(valid_until=expiry)
        self.assertEqual(expected_assets_queryset(self.session, user=self.user).count(), 1)
        with self.assertRaises(PermissionDenied):
            with patch("compliance.audit_services.timezone.now", return_value=expiry + timedelta(seconds=1)):
                expected_assets_queryset(self.session, user=self.user)


if __name__ == "__main__":
    import unittest

    unittest.main()
