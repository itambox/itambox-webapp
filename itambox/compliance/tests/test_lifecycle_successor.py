"""RED-to-GREEN lifecycle successor tests for issue #446."""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from queue import Queue
from unittest.mock import Mock, patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, connections, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker
from rest_framework.test import APIClient

from assets.models import Asset, StatusLabel
from compliance.api.serializers import AuditSessionSerializer
from compliance.audit_services import (
    audit_asset,
    close_audit_session,
    flag_missing_assets,
    rehome_audit_session_mismatches,
)
from compliance.forms_audit import AuditSessionForm
from compliance.models import AssetAudit, AuditSession
from compliance.views_audit import AuditSessionCloseView, AuditSessionFlagMissingView
from core.tests.mixins import TenantTestMixin, grant
from organization.models import Location, Role, Site, Tenant


class AuditLifecycleBoundaryTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="Lifecycle A", slug="lifecycle-a")
        self.tenant_role.permissions = [
            "compliance.view_auditsession",
            "compliance.add_auditsession",
            "compliance.add_assetaudit",
            "compliance.change_auditsession",
            "assets.change_asset",
        ]
        self.tenant_role.save(update_fields=["permissions"])
        self.site = baker.make(Site, tenant=self.tenant, name="Lifecycle site A")
        self.location_a = baker.make(Location, tenant=self.tenant, site=self.site, name="Lifecycle A")
        self.location_b = baker.make(Location, tenant=self.tenant, site=self.site, name="Lifecycle B")
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE, name="Lifecycle deployable")
        self.session = AuditSession.objects.create(
            name="Lifecycle session",
            tenant=self.tenant,
            location=self.location_a,
            status="active",
            created_by=self.tenant_user,
        )
        self.asset = baker.make(
            Asset,
            tenant=self.tenant,
            location=self.location_a,
            status=self.status,
            name="Lifecycle asset",
            asset_tag="LIFECYCLE-1",
        )

    def _close(self, *, with_audit=False):
        if with_audit:
            audit_asset(
                self.asset,
                user=self.tenant_user,
                session=self.session,
                location=self.location_b,
                status=self.status,
            )
        close_audit_session(self.session, user=self.tenant_user)
        self.session.refresh_from_db()

    def test_completed_session_model_boundary_rejects_all_frozen_fields(self):
        self._close()
        original = {
            "tenant": self.session.tenant,
            "location": self.session.location,
            "name": self.session.name,
            "status": self.session.status,
            "completed_at": self.session.completed_at,
            "reconciliation_report": self.session.reconciliation_report,
        }
        tenant_b = Tenant.objects.create(name="Lifecycle B", slug="lifecycle-b")
        changes = {
            "tenant": tenant_b,
            "location": self.location_b,
            "name": "Tampered lifecycle session",
            "status": "active",
            "completed_at": timezone.now() - timedelta(days=1),
            "reconciliation_report": {"schema_version": 2, "rows": []},
        }

        for field, value in changes.items():
            with self.subTest(field=field):
                session = AuditSession._base_manager.get(pk=self.session.pk)
                setattr(session, field, value)
                with self.assertRaises(ValidationError):
                    session.save()
                session.refresh_from_db()
                self.assertEqual(session.tenant, original["tenant"])
                self.assertEqual(session.location, original["location"])
                self.assertEqual(session.name, original["name"])
                self.assertEqual(session.status, original["status"])
                self.assertEqual(session.completed_at, original["completed_at"])
                self.assertEqual(session.reconciliation_report, original["reconciliation_report"])

    def test_completed_session_form_and_serializer_reject_location_changes(self):
        self._close()
        form = AuditSessionForm(
            instance=self.session,
            data={
                "name": self.session.name,
                "tenant": self.tenant.pk,
                "location": self.location_b.pk,
                "start_immediately": "on",
            },
            request=None,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("completed", str(form.errors).lower())

        serializer = AuditSessionSerializer(
            instance=self.session,
            data={"location_id": self.location_b.pk},
            partial=True,
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("completed", str(serializer.errors).lower())

    def test_completed_session_api_rejects_location_b_and_null(self):
        self._close()
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        client = APIClient()
        client.force_login(self.tenant_user)
        api_session = client.session
        api_session["active_tenant_id"] = self.tenant.pk
        api_session.save()
        url = reverse("api:compliance_api:auditsession-detail", kwargs={"pk": self.session.pk})

        for location in (self.location_b.pk, None):
            with self.subTest(location=location):
                response = client.patch(
                    url,
                    {"location_id": location},
                    format="json",
                    HTTP_IF_MATCH=self._api_etag(client, url),
                )
                self.assertEqual(response.status_code, 400, response.data)
                self.session.refresh_from_db()
                self.assertEqual(self.session.location_id, self.location_a.pk)

    def _api_etag(self, client, url):
        response = client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        return response["ETag"]

    def test_api_close_advances_updated_at_atomically(self):
        client = APIClient()
        client.force_login(self.tenant_user)
        api_session = client.session
        api_session["active_tenant_id"] = self.tenant.pk
        api_session.save()
        url = reverse("api:compliance_api:auditsession-detail", kwargs={"pk": self.session.pk})
        etag = self._api_etag(client, url)
        before = self.session.updated_at

        response = client.patch(url, {"status": "completed"}, format="json", HTTP_IF_MATCH=etag)

        self.assertEqual(response.status_code, 200, response.data)
        self.session.refresh_from_db()
        self.assertGreater(self.session.updated_at, before)
        self.assertEqual(self.session.reconciliation_report["rehome_location_id"], self.location_a.pk)

    def test_api_close_rejects_scope_or_metadata_changes_in_same_request(self):
        client = APIClient()
        client.force_login(self.tenant_user)
        api_session = client.session
        api_session["active_tenant_id"] = self.tenant.pk
        api_session.save()
        url = reverse("api:compliance_api:auditsession-detail", kwargs={"pk": self.session.pk})

        for extra in ({"location_id": self.location_b.pk}, {"name": "Changed while closing"}):
            with self.subTest(extra=extra):
                response = client.patch(
                    url,
                    {"status": "completed", **extra},
                    format="json",
                    HTTP_IF_MATCH=self._api_etag(client, url),
                )
                self.assertEqual(response.status_code, 400, response.data)
                self.session.refresh_from_db()
                self.assertEqual(self.session.status, "active")
                self.assertEqual(self.session.name, "Lifecycle session")
                self.assertEqual(self.session.location_id, self.location_a.pk)
                self.assertIsNone(self.session.reconciliation_report)

    def test_api_close_rejects_stale_if_match_after_external_change(self):
        client = APIClient()
        client.force_login(self.tenant_user)
        api_session = client.session
        api_session["active_tenant_id"] = self.tenant.pk
        api_session.save()
        url = reverse("api:compliance_api:auditsession-detail", kwargs={"pk": self.session.pk})
        stale_etag = self._api_etag(client, url)
        self.session.name = "Changed before close"
        self.session.save(update_fields=["name", "updated_at"])

        response = client.patch(url, {"status": "completed"}, format="json", HTTP_IF_MATCH=stale_etag)

        self.assertEqual(response.status_code, 412, response.data)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "active")
        self.assertIsNone(self.session.reconciliation_report)

    def test_close_advances_updated_at_and_freezes_rehome_location(self):
        before = self.session.updated_at
        self._close(with_audit=True)
        self.assertGreater(self.session.updated_at, before)
        report = self.session.reconciliation_report
        self.assertEqual(report["rehome_location_id"], self.location_a.pk)

        # Simulate a stale mutable relation written below the model boundary. The
        # service must remain bound to the close-time evidence, not this relation.
        AuditSession._base_manager.filter(pk=self.session.pk).update(location_id=self.location_b.pk)
        self.session.refresh_from_db()
        rehome_audit_session_mismatches(self.session, user=self.tenant_user)

        self.asset.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(self.asset.location_id, self.location_a.pk)

    def test_rehome_rejects_v1_without_mutation(self):
        self.session.status = "completed"
        self.session.reconciliation_report = {
            "rows": [{"category": "mismatched", "asset_id": self.asset.pk}],
        }
        self.session.save(update_fields=["status", "reconciliation_report"])
        before_location = self.asset.location_id
        with self.assertRaises(ValidationError):
            rehome_audit_session_mismatches(self.session, user=self.tenant_user)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.location_id, before_location)
        self.session.refresh_from_db()
        self.assertEqual(self.session.reconciliation_report["rows"][0].get("tenant_id"), None)

    def test_rehome_rejects_absent_report_without_mutation(self):
        AuditSession._base_manager.filter(pk=self.session.pk).update(
            status="completed",
            reconciliation_report=None,
        )
        self.session.refresh_from_db()
        before_location = self.asset.location_id

        with self.assertRaises(ValidationError):
            rehome_audit_session_mismatches(self.session, user=self.tenant_user)

        self.asset.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(self.asset.location_id, before_location)
        self.assertIsNone(self.session.reconciliation_report)

    def test_flag_rejects_absent_report_without_zero_count_success(self):
        AuditSession._base_manager.filter(pk=self.session.pk).update(
            status="completed",
            reconciliation_report=None,
        )
        self.session.refresh_from_db()
        before_asset = self.asset.status_id

        with self.assertRaises(ValidationError):
            flag_missing_assets(self.session, user=self.tenant_user)

        self.asset.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(self.asset.status_id, before_asset)
        self.assertIsNone(self.session.reconciliation_report)

    def test_missing_status_is_required_and_never_created_or_repaired(self):
        self._close()
        self.session.reconciliation_report = {
            "schema_version": 2,
            "rehome_location_id": self.location_a.pk,
            "total_expected": 1,
            "total_scanned": 0,
            "rows": [
                {
                    "tenant_id": self.tenant.pk,
                    "category": "missing",
                    "asset_id": self.asset.pk,
                    "status_id": self.asset.status_id,
                }
            ],
        }
        AuditSession._base_manager.filter(pk=self.session.pk).update(
            reconciliation_report=self.session.reconciliation_report
        )

        for configured_type in (None, StatusLabel.TYPE_DEPLOYABLE):
            with self.subTest(configured_type=configured_type):
                canonical = StatusLabel._base_manager.filter(slug="missing").first()
                if canonical is None:
                    canonical = StatusLabel._base_manager.create(
                        name="Missing",
                        slug="missing",
                        type=StatusLabel.TYPE_DEPLOYABLE,
                        color="dc3545",
                    )
                if configured_type is None:
                    StatusLabel._base_manager.filter(pk=canonical.pk).update(deleted_at=timezone.now())
                    expected_status = None
                else:
                    StatusLabel._base_manager.filter(pk=canonical.pk).update(
                        deleted_at=None,
                        type=configured_type,
                    )
                    expected_status = configured_type
                before_statuses = list(
                    StatusLabel._base_manager.values_list("pk", "slug", "type", "deleted_at").order_by("pk")
                )
                before_asset = self.asset.status_id
                before_session = self.session.reconciliation_report
                with self.assertRaises(ValidationError):
                    flag_missing_assets(self.session, user=self.tenant_user)
                self.asset.refresh_from_db()
                self.session.refresh_from_db()
                self.assertEqual(self.asset.status_id, before_asset)
                self.assertEqual(self.session.reconciliation_report, before_session)
                self.assertEqual(
                    list(StatusLabel._base_manager.values_list("pk", "slug", "type", "deleted_at").order_by("pk")),
                    before_statuses,
                )
                if expected_status is not None:
                    self.assertEqual(StatusLabel._base_manager.get(pk=canonical.pk).type, expected_status)

    def test_html_close_advances_updated_at(self):
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        before = self.session.updated_at
        response = self.client.post(
            reverse("compliance:auditsession_close", kwargs={"pk": self.session.pk}),
            {},
        )
        self.assertEqual(response.status_code, 302)
        self.session.refresh_from_db()
        self.assertGreater(self.session.updated_at, before)

    def test_html_close_permission_denial_is_not_a_form_error(self):
        tenant_b = Tenant.objects.create(name="Lifecycle denial B", slug="lifecycle-denial-b")
        site_b = baker.make(Site, tenant=tenant_b, name="Lifecycle denial site B")
        location_b = baker.make(Location, tenant=tenant_b, site=site_b, name="Lifecycle denial room B")
        baker.make(Asset, tenant=tenant_b, location=location_b, status=self.status, name="Lifecycle denial asset B")
        global_session = AuditSession.objects.create(
            name="Lifecycle global denial",
            tenant=None,
            status="active",
            created_by=self.tenant_user,
        )
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        url = reverse("compliance:auditsession_close", kwargs={"pk": global_session.pk})
        response = self.client.post(url, {}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(b"unexpected error", response.content.lower())
        global_session.refresh_from_db()
        self.assertEqual(global_session.status, "active")

    def test_internal_close_permission_denial_has_full_page_and_htmx_semantics(self):
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        url = reverse("compliance:auditsession_close", kwargs={"pk": self.session.pk})
        denied = Mock(side_effect=PermissionDenied("Lifecycle close denied"))

        with patch.object(AuditSessionCloseView, "service_callable", denied):
            response = self.client.post(url, {})
            self.assertEqual(response.status_code, 403)

            response = self.client.post(url, {}, HTTP_HX_REQUEST="true")
            self.assertEqual(response.status_code, 403)
            self.assertIn("Lifecycle close denied", response["HX-Trigger"])

        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "active")
        self.assertEqual(denied.call_count, 2)

    def test_internal_flag_permission_denial_has_full_page_and_htmx_semantics(self):
        self._close()
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        url = reverse("compliance:auditsession_flag_missing", kwargs={"pk": self.session.pk})
        denied = Mock(side_effect=PermissionDenied("Lifecycle flag denied"))

        with patch.object(AuditSessionFlagMissingView, "service_callable", denied):
            response = self.client.post(url, {})
            self.assertEqual(response.status_code, 403)

            response = self.client.post(url, {}, HTTP_HX_REQUEST="true")
            self.assertEqual(response.status_code, 403)
            self.assertIn("Lifecycle flag denied", response["HX-Trigger"])

        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status_id, self.status.pk)
        self.assertEqual(denied.call_count, 2)


class AuditLifecyclePreviewTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="Preview tenant", slug="preview-tenant")
        self.tenant_role.permissions = ["compliance.view_auditsession", "compliance.change_auditsession"]
        self.tenant_role.save(update_fields=["permissions"])
        self.location = baker.make(Location, tenant=self.tenant, name="Preview room")
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE, name="Preview deployable")
        self.asset = baker.make(Asset, tenant=self.tenant, location=self.location, status=self.status)
        self.session = AuditSession.objects.create(
            name="Preview session",
            tenant=self.tenant,
            location=self.location,
            status="completed",
            completed_at=timezone.now(),
            created_by=self.tenant_user,
            reconciliation_report={
                "schema_version": 2,
                "rehome_location_id": self.location.pk,
                "total_expected": 1,
                "total_scanned": 0,
                "rows": [
                    {
                        "tenant_id": self.tenant.pk,
                        "category": "missing",
                        "asset_id": self.asset.pk,
                        "status_id": self.status.pk,
                    }
                ],
            },
        )

    def test_flag_preview_uses_assets_change_asset_without_report_read(self):
        actor = baker.make("users.User", username="preview-change-only", is_active=True)
        role = Role.objects.create(tenant=self.tenant, name="Preview change role", permissions=["assets.change_asset"])
        grant(actor, self.tenant, role)
        self.client_login_to_tenant(actor, self.tenant)
        response = self.client.get(
            reverse("compliance:auditsession_flag_missing", kwargs={"pk": self.session.pk}),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertContains(response, "1")

    def test_flag_post_without_fixed_permission_is_denied(self):
        actor = baker.make("users.User", username="preview-no-change", is_active=True)
        role = Role.objects.create(
            tenant=self.tenant, name="Preview read role", permissions=["compliance.view_auditsession"]
        )
        grant(actor, self.tenant, role)
        self.client_login_to_tenant(actor, self.tenant)
        response = self.client.post(
            reverse("compliance:auditsession_flag_missing", kwargs={"pk": self.session.pk}),
            {},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 403)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status_id, self.status.pk)


class AuditLifecyclePostgresRaceTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Race tenant", slug="race-tenant")
        self.user = baker.make("users.User", username="race-user", is_active=True)
        role = Role.objects.create(
            tenant=self.tenant,
            name="Race role",
            permissions=["compliance.change_auditsession", "compliance.add_assetaudit"],
        )
        grant(self.user, self.tenant, role)
        self.location = baker.make(Location, tenant=self.tenant, name="Race room")
        self.status = StatusLabel._base_manager.get_or_create(
            slug="available",
            defaults={"name": "Available", "type": StatusLabel.TYPE_DEPLOYABLE, "color": "28a745"},
        )[0]
        self.asset = baker.make(Asset, tenant=self.tenant, location=self.location, status=self.status)
        self.session = AuditSession.objects.create(
            name="Race session",
            tenant=self.tenant,
            location=self.location,
            status="active",
            created_by=self.user,
        )

    def _wait_for_lock(self, pid_queue, needle):
        pid = pid_queue.get(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT wait_event_type, query FROM pg_stat_activity WHERE pid = %s",
                    [pid],
                )
                row = cursor.fetchone()
            if row and row[0] == "Lock" and "FOR UPDATE" in row[1].upper() and needle in row[1].lower():
                return
            time.sleep(0.01)
        self.fail(f"backend {pid} did not reach the expected row lock")

    def test_scan_waits_for_close_and_cannot_commit_after_frozen_report(self):
        if connection.vendor != "postgresql":
            self.skipTest("This regression requires PostgreSQL row-lock semantics.")

        close_errors = Queue()
        scan_errors = Queue()
        close_pid = Queue()
        scan_pid = Queue()

        def close_worker():
            db = connections["default"]
            db.close()
            db.ensure_connection()
            close_pid.put(db.connection.get_backend_pid())
            try:
                close_audit_session(self.session, user=self.user)
            except Exception as exc:  # propagate real worker failure to the assertion thread
                close_errors.put(exc)
            finally:
                db.close()

        def scan_worker():
            db = connections["default"]
            db.close()
            db.ensure_connection()
            scan_pid.put(db.connection.get_backend_pid())
            try:
                audit_asset(
                    self.asset,
                    user=self.user,
                    session=self.session,
                    location=self.location,
                    status=self.status,
                )
            except Exception as exc:
                scan_errors.put(exc)
            finally:
                db.close()

        close_thread = threading.Thread(target=close_worker)
        scan_thread = threading.Thread(target=scan_worker)
        with transaction.atomic():
            Asset._base_manager.select_for_update().get(pk=self.asset.pk)
            close_thread.start()
            self._wait_for_lock(close_pid, "auditsession")
            scan_thread.start()
            scan_pid.get(timeout=5)
        close_thread.join(timeout=5)
        scan_thread.join(timeout=5)

        self.assertFalse(close_thread.is_alive())
        self.assertFalse(scan_thread.is_alive())
        self.assertTrue(close_errors.empty(), list(close_errors.queue))
        self.assertFalse(scan_errors.empty())
        self.assertIsInstance(scan_errors.get(), ValidationError)
        self.assertFalse(AssetAudit.objects.filter(session=self.session, asset=self.asset).exists())
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "completed")
        self.assertEqual(self.session.reconciliation_report["total_scanned"], 0)
