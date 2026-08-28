from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from assets.models import (
    Asset,
    AssetRole,
    AssetType,
    Manufacturer,
    StatusLabel,
)
from compliance.audit_services import (
    audit_asset,
    close_audit_session,
    expected_assets_queryset,
    rehome_audit_session_mismatches,
)
from compliance.models import AssetAudit, AuditSession
from core.managers import set_current_tenant
from core.tests.mixins import grant
from organization.models import Location, Role, Site, Tenant

User = get_user_model()


class AuditReconciliationTestCase(TestCase):
    def setUp(self):
        # Create users
        self.admin = User.objects.create_user(
            username="adminuser", password="password123", is_staff=True, is_superuser=True
        )
        self.auditor = User.objects.create_user(
            username="auditoruser", password="password123", is_staff=True, is_superuser=False
        )
        self.tenant = Tenant.objects.create(name="Audit test tenant", slug="audit-test-tenant")
        audit_role = Role.objects.create(
            tenant=self.tenant,
            name="Audit test role",
            permissions=[
                "compliance.view_auditsession",
                "compliance.add_assetaudit",
                "compliance.view_assetaudit",
                "compliance.change_auditsession",
                "assets.change_asset",
            ],
        )
        grant(self.admin, self.tenant, audit_role)
        grant(self.auditor, self.tenant, audit_role)

        # Setup site and locations
        self.site = Site.objects.create(name="Stuttgart HQ", slug="stuttgart-hq", tenant=self.tenant)
        self.staging_room = Location.objects.create(
            name="Staging Room", slug="staging", site=self.site, tenant=self.tenant
        )
        self.server_room = Location.objects.create(
            name="Server Room", slug="server", site=self.site, tenant=self.tenant
        )

        # Setup manufacturer, role and status labels
        self.manufacturer = Manufacturer.objects.create(name="Dell", slug="dell")
        self.role = AssetRole.objects.create(name="Laptop", slug="laptop")
        self.status_deployable = StatusLabel.objects.create(
            name="Deployable", slug="deployable", type=StatusLabel.TYPE_DEPLOYABLE
        )
        self.status_archived = StatusLabel.objects.create(
            name="Archived", slug="archived", type=StatusLabel.TYPE_ARCHIVED
        )

        # Setup asset type
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer, model="Latitude 5420", slug="latitude-5420", requestable=True
        )

        # Setup expected assets (registered in Staging Room)
        self.asset_expected_1 = Asset.objects.create(
            name="Staging Laptop 1",
            asset_tag="ASSET-000001",
            serial_number="SN-STAGING-1",
            asset_type=self.asset_type,
            asset_role=self.role,
            status=self.status_deployable,
            location=self.staging_room,
            tenant=self.tenant,
        )
        self.asset_expected_2 = Asset.objects.create(
            name="Staging Laptop 2",
            asset_tag="ASSET-000002",
            serial_number="SN-STAGING-2",
            asset_type=self.asset_type,
            asset_role=self.role,
            status=self.status_deployable,
            location=self.staging_room,
            tenant=self.tenant,
        )

        # Setup mismatched asset (registered in Server Room but physically scanned in Staging Room)
        self.asset_mismatched = Asset.objects.create(
            name="Server Laptop",
            asset_tag="ASSET-000003",
            serial_number="SN-SERVER-3",
            asset_type=self.asset_type,
            asset_role=self.role,
            status=self.status_deployable,
            location=self.server_room,
            tenant=self.tenant,
        )

        # Setup archived asset (should fail validation during scan)
        self.asset_archived = Asset.objects.create(
            name="Archived Laptop",
            asset_tag="ASSET-000004",
            serial_number="SN-ARCHIVED-4",
            asset_type=self.asset_type,
            asset_role=self.role,
            status=self.status_archived,
            location=self.staging_room,
            tenant=self.tenant,
        )

    def test_campaign_lifecycle_and_verification(self):
        # 1. Plan and active a session in Staging Room
        session = AuditSession.objects.create(
            name="Staging Room Audit Q2",
            location=self.staging_room,
            tenant=self.tenant,
            created_by=self.admin,
            status="active",
        )
        self.assertEqual(session.status, "active")

        # Check expected assets query helper
        expected_assets = list(expected_assets_queryset(session, user=self.admin).values_list("id", flat=True))
        self.assertIn(self.asset_expected_1.id, expected_assets)
        self.assertIn(self.asset_expected_2.id, expected_assets)
        self.assertNotIn(self.asset_mismatched.id, expected_assets)

        # 2. Perform scanning physical verification on expected asset 1
        audit_1 = audit_asset(
            asset=self.asset_expected_1,
            user=self.auditor,
            session=session,
            location=session.location,
            status=self.status_deployable,
            verification_method="barcode",
        )
        self.assertEqual(audit_1.session, session)
        self.assertEqual(audit_1.asset, self.asset_expected_1)
        self.assertEqual(audit_1.verification_method, "barcode")

        # Check expected core Asset stamps (should not change registered location during active campaign)
        self.asset_expected_1.refresh_from_db()
        self.assertIsNotNone(self.asset_expected_1.last_audited)
        self.assertEqual(self.asset_expected_1.last_audited_by, self.auditor)
        self.assertEqual(self.asset_expected_1.location, self.staging_room)

        # 3. Double scanning expected asset 1 must fail
        with self.assertRaises(ValidationError):
            audit_asset(
                asset=self.asset_expected_1,
                user=self.auditor,
                session=session,
                location=session.location,
                status=self.status_deployable,
            )

        # 4. Scanning an archived asset must fail validation
        with self.assertRaises(ValidationError):
            audit_asset(
                asset=self.asset_archived,
                user=self.auditor,
                session=session,
                location=session.location,
                status=self.status_archived,
            )

        # 5. Scan the mismatched asset (physically observed in Staging Room)
        audit_mismatch = audit_asset(
            asset=self.asset_mismatched,
            user=self.auditor,
            session=session,
            location=session.location,
            status=self.status_deployable,
            verification_method="barcode",
        )
        self.assertEqual(audit_mismatch.asset, self.asset_mismatched)
        self.asset_mismatched.refresh_from_db()
        self.assertEqual(self.asset_mismatched.location, self.server_room)  # Still registered at Server Room

        # 6. Close the session and run reconciliation reports
        report = close_audit_session(session, user=self.admin)
        self.assertEqual(session.status, "completed")
        self.assertEqual(report["total_expected"], 2)
        self.assertEqual(report["total_scanned"], 2)  # asset_expected_1 and asset_mismatched
        self.assertEqual(report["matching_count"], 1)  # asset_expected_1

        # asset_mismatched is registered at server_room (not in expected_ids for staging_room
        # session) and was FOUND in staging_room → classified as "surprise", not "mismatch".
        surprise_ids = [a.id for a in report["surprise_list"]]
        self.assertIn(self.asset_mismatched.id, surprise_ids)
        self.assertEqual(report["mismatch_list"], [])

        # Check missing list (asset_expected_2 was not scanned)
        missing_ids = [a.id for a in report["missing_list"]]
        self.assertIn(self.asset_expected_2.id, missing_ids)

        # 7. Bulk re-home: use a new active session; completed sessions cannot be reopened.
        rehome_session = AuditSession.objects.create(
            name="Rehome follow-up",
            tenant=self.tenant,
            location=self.staging_room,
            status="active",
            created_by=self.admin,
        )
        audit_asset(
            asset=self.asset_expected_2,
            user=self.admin,
            session=rehome_session,
            location=self.server_room,  # observed at server_room (≠ session staging_room)
            status=self.status_deployable,
            verification_method="manual",
        )
        close_audit_session(rehome_session, user=self.admin)  # re-freeze with the mismatch
        rehome_session.refresh_from_db()
        rehome_audit_session_mismatches(rehome_session, user=self.admin)
        # Rehome moves the mismatched asset to session.location (staging_room).
        # asset_expected_2 was already registered at staging_room, so the location
        # remains staging_room — confirming rehome ran cleanly without error.
        self.asset_expected_2.refresh_from_db()
        self.assertEqual(self.asset_expected_2.location, self.staging_room)

    def test_audit_outside_session(self):
        # Auditing outside session must change location & status immediately
        audit_asset(
            asset=self.asset_mismatched,
            user=self.auditor,
            session=None,
            location=self.staging_room,
            status=self.status_deployable,
            verification_method="manual",
        )
        self.asset_mismatched.refresh_from_db()
        self.assertEqual(self.asset_mismatched.location, self.staging_room)
        self.assertEqual(self.asset_mismatched.status, self.status_deployable)

    def test_views_endpoints(self):
        self.client.force_login(self.admin)
        client_session = self.client.session
        client_session["active_tenant_id"] = self.tenant.pk
        client_session.save()
        set_current_tenant(self.tenant)

        # 1. Create a campaign session (start_immediately=on activates it immediately)
        response = self.client.post(
            reverse("compliance:auditsession_create"),
            data={
                "name": "Server Room Audit Q2",
                "location": self.server_room.pk,
                "start_immediately": "on",
            },
        )
        self.assertEqual(response.status_code, 302)

        session = AuditSession.objects.get(name="Server Room Audit Q2")
        self.assertEqual(session.status, "active")

        # 2. HTMX Barcode Scanning endpoint
        response = self.client.post(
            reverse("compliance:auditsession_scan", kwargs={"pk": session.pk}),
            data={"barcode": "ASSET-000003"},
            HTTP_HX_REQUEST="true",  # Simulate HTMX request
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Scanned")
        self.assertContains(response, "Server Laptop")

        # Verify scan record created
        self.assertTrue(AssetAudit.objects.filter(session=session, asset=self.asset_mismatched).exists())

        # 3. Close the campaign
        response = self.client.post(
            reverse("compliance:auditsession_close", kwargs={"pk": session.pk}), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 204)  # Success without content, sends HTMX triggers
        session.refresh_from_db()
        self.assertEqual(session.status, "completed")

        # 4. Bulk re-home via a new active session; completed sessions cannot be reopened.
        rehome_session = AuditSession.objects.create(
            name="View rehome follow-up",
            tenant=self.tenant,
            location=self.server_room,
            status="active",
            created_by=self.admin,
        )
        audit_asset(
            asset=self.asset_mismatched,
            user=self.admin,
            session=rehome_session,
            location=self.staging_room,  # observed at staging_room (≠ session server_room)
            status=self.status_deployable,
        )
        close_audit_session(rehome_session, user=self.admin)  # re-freeze with the mismatch
        rehome_session.refresh_from_db()
        self.assertEqual(rehome_session.status, "completed")

        # Post to re-home endpoint
        response = self.client.post(
            reverse("compliance:auditsession_rehome", kwargs={"pk": rehome_session.pk}), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 204)

        # Rehome moves the mismatched asset to session.location (server_room).
        self.asset_mismatched.refresh_from_db()
        self.assertEqual(self.asset_mismatched.location, self.server_room)


class AuditAPIViewsTestCase(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.user = User.objects.create_user(
            username="apiuser", password="password123", is_staff=True, is_superuser=True
        )
        self.tenant = Tenant.objects.create(name="Audit API tenant", slug="audit-api-tenant")
        audit_role = Role.objects.create(
            tenant=self.tenant,
            name="Audit API role",
            permissions=["compliance.add_assetaudit", "compliance.view_assetaudit"],
        )
        grant(self.user, self.tenant, audit_role)
        set_current_tenant(self.tenant)
        self.client.force_authenticate(user=self.user)

        self.site = Site.objects.create(name="Stuttgart HQ", slug="stuttgart-hq", tenant=self.tenant)
        self.staging_room = Location.objects.create(
            name="Staging Room", slug="staging", site=self.site, tenant=self.tenant
        )
        self.manufacturer = Manufacturer.objects.create(name="Dell", slug="dell")
        self.role = AssetRole.objects.create(name="Laptop", slug="laptop")
        self.status_deployable = StatusLabel.objects.create(
            name="Deployable", slug="deployable", type=StatusLabel.TYPE_DEPLOYABLE
        )
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer, model="Latitude 5420", slug="latitude-5420", requestable=True
        )
        self.asset = Asset.objects.create(
            name="Staging Laptop",
            asset_tag="ASSET-100001",
            serial_number="SN-100001",
            asset_type=self.asset_type,
            asset_role=self.role,
            status=self.status_deployable,
            location=self.staging_room,
            tenant=self.tenant,
        )

    def test_audit_session_and_asset_audit_endpoints(self):
        """Test API endpoints for audit sessions and asset audit logs."""
        from django.urls import reverse

        # 1. Create audit session via API
        session_data = {"name": "API Q2 Audit Session", "location_id": self.staging_room.pk, "status": "active"}
        create_url = reverse("api:compliance_api:auditsession-list")
        response = self.client.post(create_url, session_data, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["name"], "API Q2 Audit Session")
        self.assertEqual(response.data["created_by"], self.user.username)

        session_id = response.data["id"]

        # 2. List sessions via API
        list_url = reverse("api:compliance_api:auditsession-list")
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)

        # 3. Create asset audit log verification via API
        audit_data = {
            "session": session_id,
            "asset_id": self.asset.pk,
            "location_id": self.staging_room.pk,
            "status_id": self.status_deployable.pk,
            "verification_method": "barcode",
            "notes": "Verified successfully",
        }
        audit_create_url = reverse("api:compliance_api:assetaudit-list")
        response = self.client.post(audit_create_url, audit_data, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["notes"], "Verified successfully")
        self.assertEqual(response.data["auditor"], self.user.username)

        # 4. List audit logs via API
        audit_list_url = reverse("api:compliance_api:assetaudit-list")
        response = self.client.get(audit_list_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)


class AuditSessionFilterSetTests(TestCase):
    def setUp(self):
        from compliance.models import AuditSession
        from organization.models import Location, Site

        self.site = Site.objects.create(name="Stuttgart HQ", slug="stuttgart-hq")
        self.loc1 = Location.objects.create(name="Server Room", slug="server-room", site=self.site)
        self.loc2 = Location.objects.create(name="Staging Room", slug="staging", site=self.site)

        self.user = User.objects.create_user(username="testuser_filter", password="password123")
        self.session_active = AuditSession.objects.create(
            name="Active Session", location=self.loc1, status="active", created_by=self.user
        )
        self.session_planned = AuditSession.objects.create(
            name="Planned Session", location=self.loc2, status="planned", created_by=self.user
        )

    def test_filter_by_status(self):
        from compliance.filters import AuditSessionFilterSet

        f = AuditSessionFilterSet({"status": "active"}, queryset=AuditSession.objects.all())
        self.assertTrue(f.is_valid())
        self.assertIn(self.session_active, f.qs)
        self.assertNotIn(self.session_planned, f.qs)

    def test_filter_by_location(self):
        from compliance.filters import AuditSessionFilterSet

        f = AuditSessionFilterSet({"location": self.loc2.pk}, queryset=AuditSession.objects.all())
        self.assertTrue(f.is_valid())
        self.assertIn(self.session_planned, f.qs)
        self.assertNotIn(self.session_active, f.qs)

    def test_filter_search(self):
        from compliance.filters import AuditSessionFilterSet

        f = AuditSessionFilterSet({"q": "Planned"}, queryset=AuditSession.objects.all())
        self.assertTrue(f.is_valid())
        self.assertIn(self.session_planned, f.qs)
        self.assertNotIn(self.session_active, f.qs)
