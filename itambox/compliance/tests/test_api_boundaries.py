"""API and operation-scope contracts for issue #446."""

from django.test import TestCase
from django.urls import reverse
from model_bakery import baker
from rest_framework.test import APIClient

from assets.models import Asset, AssetRole, AssetType, Manufacturer, StatusLabel
from compliance.models import AssetAudit, AuditSession
from core.tests.mixins import TenantTestMixin
from organization.models import Location, Site, Tenant


class ComplianceAPIBoundaryTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="Audit API tenant", slug="audit-api-boundary")
        self.tenant_role.permissions = [
            "compliance.view_auditsession",
            "compliance.add_auditsession",
            "compliance.add_assetaudit",
            "compliance.change_assetaudit",
            "compliance.change_auditsession",
            "assets.change_asset",
        ]
        self.tenant_role.save(update_fields=["permissions"])
        self.client = APIClient()
        self.client_login_to_tenant(self.tenant_user, self.tenant)

        self.site = baker.make(Site, tenant=self.tenant, name="API site")
        self.location = baker.make(Location, tenant=self.tenant, site=self.site, name="API room")
        self.other_location = baker.make(Location, tenant=self.tenant, site=self.site, name="API other room")
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE, name="API deployable")
        self.manufacturer = baker.make(Manufacturer, name="API manufacturer")
        self.asset_role = baker.make(AssetRole, name="API asset role")
        self.asset_type = baker.make(
            AssetType,
            manufacturer=self.manufacturer,
            model="API model",
            requestable=True,
        )
        self.asset = baker.make(
            Asset,
            tenant=self.tenant,
            location=self.location,
            status=self.status,
            asset_type=self.asset_type,
            asset_role=self.asset_role,
            asset_tag="API-ASSET-1",
            serial_number="API-SERIAL-1",
            name="API asset one",
        )

    def _session_list_url(self):
        return reverse("api:compliance_api:auditsession-list")

    def _session_detail_url(self, session):
        return reverse("api:compliance_api:auditsession-detail", kwargs={"pk": session.pk})

    def _audit_list_url(self):
        return reverse("api:compliance_api:assetaudit-list")

    def _audit_detail_url(self, audit):
        return reverse("api:compliance_api:assetaudit-detail", kwargs={"pk": audit.pk})

    def _create_session(self, status="active"):
        response = self.client.post(
            self._session_list_url(),
            {"name": f"API {status} session", "location_id": self.location.pk, "status": status},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return AuditSession.objects.get(pk=response.data["id"]), response

    def _etag(self, session):
        response = self.client.get(self._session_detail_url(session))
        self.assertEqual(response.status_code, 200, response.data)
        self.set_active_tenant(self.tenant, self.tenant_membership)
        return response["ETag"]

    def _create_audit(self, session, asset=None):
        asset = asset or self.asset
        response = self.client.post(
            self._audit_list_url(),
            {
                "session": session.pk,
                "asset_id": asset.pk,
                "location_id": asset.location_id,
                "status_id": asset.status_id,
                "verification_method": "manual",
                "notes": "API observation",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return AssetAudit.objects.get(pk=response.data["id"])

    def test_api_close_uses_service_and_persists_v2_report(self):
        session, _response = self._create_session()
        self._create_audit(session)

        response = self.client.patch(
            self._session_detail_url(session),
            {"status": "completed", "completed_at": "2000-01-01T00:00:00Z"},
            format="json",
            HTTP_IF_MATCH=self._etag(session),
        )

        self.assertEqual(response.status_code, 200, response.data)
        session.refresh_from_db()
        self.assertEqual(session.status, "completed")
        self.assertIsNotNone(session.completed_at)
        self.assertNotEqual(session.completed_at.isoformat()[:10], "2000-01-01")
        self.assertEqual(session.reconciliation_report["schema_version"], 2)
        self.assertEqual(session.reconciliation_report["total_expected"], 1)
        self.assertEqual(session.reconciliation_report["total_scanned"], 1)
        self.assertEqual(session.reconciliation_report["rows"][0]["tenant_id"], self.tenant.pk)

    def test_api_create_completed_is_rejected_and_completed_session_cannot_reopen(self):
        response = self.client.post(
            self._session_list_url(),
            {"name": "API forbidden completed", "location_id": self.location.pk, "status": "completed"},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(AuditSession.objects.filter(name="API forbidden completed").exists())

        session, _response = self._create_session()
        audit = self._create_audit(session)
        close_response = self.client.patch(
            self._session_detail_url(session),
            {"status": "completed"},
            format="json",
            HTTP_IF_MATCH=self._etag(session),
        )
        self.assertEqual(close_response.status_code, 200, close_response.data)
        session.refresh_from_db()
        report_before = session.reconciliation_report

        response = self.client.patch(
            self._session_detail_url(session),
            {"status": "active"},
            format="json",
            HTTP_IF_MATCH=self._etag(session),
        )
        self.assertEqual(response.status_code, 400, response.data)
        session.refresh_from_db()
        self.assertEqual(session.status, "completed")
        self.assertEqual(session.reconciliation_report, report_before)
        audit.refresh_from_db()

    def test_api_global_close_denies_partial_coverage_without_mutation(self):
        tenant_b = Tenant.objects.create(name="API foreign tenant", slug="api-foreign-tenant")
        site_b = baker.make(Site, tenant=tenant_b, name="Foreign API site")
        location_b = baker.make(Location, tenant=tenant_b, site=site_b, name="Foreign API room")
        foreign_asset = baker.make(
            Asset,
            tenant=tenant_b,
            location=location_b,
            status=self.status,
            asset_type=self.asset_type,
            asset_role=self.asset_role,
            asset_tag="API-FOREIGN-1",
            name="Foreign API asset",
        )
        global_actor = baker.make("users.User", username="api-global-actor", is_active=True, is_superuser=True)
        global_role = baker.make(
            "organization.Role",
            tenant=self.tenant,
            name="API global close role",
            permissions=["compliance.view_auditsession", "compliance.change_auditsession"],
        )
        self.grant(global_actor, self.tenant, global_role)
        self.client_login_to_tenant(global_actor, self.tenant)
        session = AuditSession.objects.create(
            name="API global partial session",
            tenant=None,
            status="active",
            created_by=global_actor,
        )
        before = (session.status, session.completed_at, session.reconciliation_report)

        response = self.client.patch(
            self._session_detail_url(session),
            {"status": "completed"},
            format="json",
            HTTP_IF_MATCH=self._etag(session),
        )

        self.assertEqual(response.status_code, 403, response.data)
        session.refresh_from_db()
        self.assertEqual((session.status, session.completed_at, session.reconciliation_report), before)
        foreign_asset.refresh_from_db()
        self.assertEqual(foreign_asset.location_id, location_b.pk)

    def test_api_asset_audit_create_uses_service_and_provenance_update_is_denied(self):
        session, _response = self._create_session()
        audit = self._create_audit(session)

        response = self.client.patch(
            self._audit_detail_url(audit),
            {"location_id": self.other_location.pk},
            format="json",
            HTTP_IF_MATCH="audit-provenance-test",
        )

        self.assertEqual(response.status_code, 400, response.data)
        audit.refresh_from_db()
        self.assertEqual(audit.location_id, self.asset.location_id)

    def test_api_asset_audit_rejects_completed_session_and_foreign_asset(self):
        session, _response = self._create_session()
        close_response = self.client.patch(
            self._session_detail_url(session),
            {"status": "completed"},
            format="json",
            HTTP_IF_MATCH=self._etag(session),
        )
        self.assertEqual(close_response.status_code, 200, close_response.data)

        response = self.client.post(
            self._audit_list_url(),
            {
                "session": session.pk,
                "asset_id": self.asset.pk,
                "location_id": self.asset.location_id,
                "status_id": self.asset.status_id,
                "verification_method": "manual",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(AssetAudit.objects.filter(session=session, asset=self.asset).exists())

        tenant_b = Tenant.objects.create(name="API audit foreign", slug="api-audit-foreign")
        location_b = baker.make(Location, tenant=tenant_b, name="Foreign audit room")
        foreign_asset = baker.make(
            Asset,
            tenant=tenant_b,
            location=location_b,
            status=self.status,
            asset_type=self.asset_type,
            asset_role=self.asset_role,
            asset_tag="API-AUDIT-FOREIGN",
        )
        active_session, _response = self._create_session()
        response = self.client.post(
            self._audit_list_url(),
            {
                "session": active_session.pk,
                "asset_id": foreign_asset.pk,
                "location_id": self.location.pk,
                "status_id": self.status.pk,
                "verification_method": "manual",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(AssetAudit.objects.filter(session=active_session, asset=foreign_asset).exists())


class ComplianceScanScopeTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="Scan tenant A", slug="scan-tenant-a")
        self.tenant_role.permissions = ["compliance.add_assetaudit"]
        self.tenant_role.save(update_fields=["permissions"])
        self.tenant_b = Tenant.objects.create(name="Scan tenant B", slug="scan-tenant-b")
        self.site_a = baker.make(Site, tenant=self.tenant, name="Scan site A")
        self.site_b = baker.make(Site, tenant=self.tenant_b, name="Scan site B")
        self.location_a = baker.make(Location, tenant=self.tenant, site=self.site_a, name="Scan room A")
        self.location_b = baker.make(Location, tenant=self.tenant_b, site=self.site_b, name="Scan room B")
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE, name="Scan deployable")
        manufacturer = baker.make(Manufacturer, name="Scan manufacturer")
        role = baker.make(AssetRole, name="Scan role")
        asset_type = baker.make(AssetType, manufacturer=manufacturer, model="Scan model")
        self.asset_a = baker.make(
            Asset,
            tenant=self.tenant,
            location=self.location_a,
            status=self.status,
            asset_type=asset_type,
            asset_role=role,
            asset_tag="SCAN-A",
            name="Scan tenant A asset",
        )
        self.asset_b = baker.make(
            Asset,
            tenant=self.tenant_b,
            location=self.location_b,
            status=self.status,
            asset_type=asset_type,
            asset_role=role,
            asset_tag="SCAN-B",
            name="Scan tenant B asset",
        )
        self.client = APIClient()

    def _session_url(self, name, session):
        return reverse(f"compliance:auditsession_{name}", kwargs={"pk": session.pk})

    def test_scan_only_actor_cannot_resolve_foreign_asset(self):
        session = AuditSession.objects.create(
            name="Tenant A scan",
            tenant=self.tenant,
            location=self.location_a,
            status="active",
            created_by=self.tenant_user,
        )
        self.client_login_to_tenant(self.tenant_user, self.tenant)

        response = self.client.get(self._session_url("validate", session), {"code": self.asset_b.asset_tag})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"found": False})

        response = self.client.post(
            self._session_url("scan", session),
            {"barcode": self.asset_b.asset_tag},
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(self.asset_b.name.encode(), response.content)
        self.assertFalse(AssetAudit.objects.filter(session=session, asset=self.asset_b).exists())

    def test_scan_only_all_accessible_actor_resolves_a_and_b_for_validate_scan_and_commit(self):
        scan_user = baker.make("users.User", username="scan-both", is_active=True)
        role_a = baker.make(
            "organization.Role",
            tenant=self.tenant,
            name="Scan A role",
            permissions=["compliance.add_assetaudit"],
        )
        role_b = baker.make(
            "organization.Role",
            tenant=self.tenant_b,
            name="Scan B role",
            permissions=["compliance.add_assetaudit"],
        )
        self.grant(scan_user, self.tenant, role_a)
        self.grant(scan_user, self.tenant_b, role_b)
        session = AuditSession.objects.create(
            name="Global scan",
            tenant=None,
            location=None,
            status="active",
            created_by=scan_user,
        )
        self.client_login_to_tenant(scan_user, self.tenant)

        response = self.client.get(self._session_url("validate", session), {"code": self.asset_b.asset_tag})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["pk"], self.asset_b.pk)

        response = self.client.post(
            self._session_url("scan", session),
            {"barcode": self.asset_b.asset_tag},
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(AssetAudit.objects.filter(session=session, asset=self.asset_b).exists())

        second_b = baker.make(
            Asset,
            tenant=self.tenant_b,
            location=self.location_b,
            status=self.status,
            asset_type=self.asset_b.asset_type,
            asset_role=self.asset_b.asset_role,
            asset_tag="SCAN-B-2",
            name="Scan tenant B asset two",
        )
        response = self.client.post(
            self._session_url("commit", session),
            {"pk": [second_b.pk]},
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(AssetAudit.objects.filter(session=session, asset=second_b).exists())
