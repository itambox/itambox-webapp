"""
Tests for Part 4: standalone verify-confirm modal (AssetAuditView).
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from model_bakery import baker

from assets.models import Asset, StatusLabel
from compliance.audit_services import audit_asset_from_form
from compliance.models import AssetAudit, AuditSession
from core.managers import set_current_tenant
from core.tests.mixins import TenantTestMixin, grant
from organization.models import Location, Role, Tenant

User = get_user_model()


class AuditAssetFromFormTests(TestCase):
    """audit_asset_from_form() session-attach logic (F2 fix)."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Modal service tenant", slug="modal-service-tenant")
        self.user = User.objects.create_user(username="modal_user", email="m@test.com", password="pw")
        role = Role.objects.create(
            tenant=self.tenant,
            name="Modal test role",
            permissions=["compliance.add_assetaudit", "compliance.view_auditsession"],
        )
        grant(self.user, self.tenant, role)
        set_current_tenant(self.tenant)
        self.loc_berlin = baker.make(Location, name="Berlin", tenant=self.tenant)
        self.loc_munich = baker.make(Location, name="Munich", tenant=self.tenant)
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        self.asset = baker.make(Asset, status=self.status, location=self.loc_berlin, tenant=self.tenant)

    def test_attaches_to_location_session(self):
        """Asset at Berlin → attaches to Berlin campaign, not Munich campaign."""
        berlin_session = AuditSession.objects.create(
            name="Berlin",
            status="active",
            location=self.loc_berlin,
            tenant=self.tenant,
            created_by=self.user,
        )
        AuditSession.objects.create(
            name="Munich",
            status="active",
            location=self.loc_munich,
            tenant=self.tenant,
            created_by=self.user,
        )
        result = audit_asset_from_form(self.asset, self.user, location=self.loc_berlin, status=self.status)
        self.assertEqual(result["session"], berlin_session)
        audit = AssetAudit.objects.get(asset=self.asset)
        self.assertEqual(audit.session, berlin_session)
        self.assertEqual(audit.location, self.loc_berlin)

    def test_no_session_when_only_different_location_campaign_exists(self):
        """F2 fix: Munich-only campaign → no attach for Berlin asset."""
        AuditSession.objects.create(
            name="Munich",
            status="active",
            location=self.loc_munich,
            tenant=self.tenant,
            created_by=self.user,
        )
        result = audit_asset_from_form(self.asset, self.user, location=self.loc_berlin, status=self.status)
        self.assertIsNone(result["session"])
        audit = AssetAudit.objects.get(asset=self.asset)
        self.assertIsNone(audit.session)

    def test_attaches_to_global_session(self):
        """Global campaign (no location) → attaches regardless of observed location."""
        global_session = AuditSession.objects.create(
            name="Global Stocktake",
            status="active",
            location=None,
            tenant=None,
            created_by=self.user,
        )
        result = audit_asset_from_form(self.asset, self.user, location=self.loc_munich, status=self.status)
        self.assertEqual(result["session"], global_session)
        audit = AssetAudit.objects.get(asset=self.asset)
        self.assertEqual(audit.session, global_session)

    def test_location_from_form_stored_on_audit(self):
        """Observed location (user-supplied) is frozen on the audit record."""
        audit_asset_from_form(self.asset, self.user, location=self.loc_munich, status=self.status)
        audit = AssetAudit.objects.get(asset=self.asset)
        self.assertEqual(audit.location, self.loc_munich)


class VerifyModalViewTests(TenantTestMixin, TestCase):
    """AssetAuditView GET renders modal; POST creates audit and OOB-swaps badge."""

    def setUp(self):
        self.setup_tenant_context(name="Modal Tenant", slug="modal-tenant")
        self.tenant_role.permissions = ["compliance.add_assetaudit", "assets.view_asset"]
        self.tenant_role.save()
        self.loc = baker.make(Location, name="ModalRoom", tenant=self.tenant)
        self.status = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        self.asset = baker.make(Asset, tenant=self.tenant, location=self.loc, status=self.status)
        self.url = reverse("assets:asset_audit", kwargs={"pk": self.asset.pk})

    def test_get_renders_modal(self):
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "asset-audit-modal")
        self.assertContains(response, "Verify Physical Presence")

    def test_post_htmx_success_returns_oob_swap(self):
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        response = self.client.post(
            self.url,
            data={"location": self.loc.pk, "status": self.status.pk},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("closeModalEvent", response.get("HX-Trigger", ""))
        self.assertIn("playAuditSound", response.get("HX-Trigger", ""))
        self.asset.refresh_from_db()
        self.assertIsNotNone(self.asset.last_audited)

    def test_post_missing_location_returns_form_error(self):
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        response = self.client.post(
            self.url,
            data={"status": self.status.pk},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(AssetAudit.objects.filter(asset=self.asset).count(), 0)

    def test_anonymous_denied(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (302, 401, 403))
