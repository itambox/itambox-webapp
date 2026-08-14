"""Tests for the scheduled-report cross-tenant scope approval view (WP-9b)."""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.tests.mixins import grant
from extras.models import ReportTemplate, ScheduledReport, ScheduledReportScopeAuthorization
from organization.models import Role, Tenant

User = get_user_model()


@override_settings(REPORT_DESIGNER_ENABLED=True)
class ScheduledReportScopeApprovalViewTests(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Approval Tenant A", slug="approval-tenant-a")
        self.tenant_b = Tenant.objects.create(name="Approval Tenant B", slug="approval-tenant-b")
        self.tenant_c = Tenant.objects.create(name="Approval Tenant C", slug="approval-tenant-c")
        self.admin = User.objects.create_superuser(username="scope-admin", password="password123", email="a@b.com")
        self.operator = User.objects.create_user(username="scope-operator", password="password123")
        self.partial = User.objects.create_user(username="scope-partial", password="password123")
        cross_tenant = ["reports.view_cross_tenant_reports"]
        grant(
            self.operator,
            self.tenant_a,
            Role.objects.create(tenant=self.tenant_a, name="CT A", permissions=cross_tenant),
        )
        grant(
            self.operator,
            self.tenant_b,
            Role.objects.create(tenant=self.tenant_b, name="CT B", permissions=cross_tenant),
        )
        grant(
            self.partial,
            self.tenant_a,
            Role.objects.create(tenant=self.tenant_a, name="CT A partial", permissions=cross_tenant),
        )
        self.template = ReportTemplate.objects.create(
            name="Approval Template",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            tenant=self.tenant_a,
        )
        self.sched = ScheduledReport.objects.create(
            name="Cross-Tenant Schedule",
            report=self.template,
            tenant=self.tenant_a,
            format=ScheduledReport.FORMAT_HTML,
            save_to_archive=False,
            is_active=True,
        )
        self.sched.filter_tenants.add(self.tenant_a, self.tenant_b)
        self.url = reverse("extras:scheduledreport_scope_approval", kwargs={"pk": self.sched.pk})

    def _client_for(self, user):
        client = Client()
        client.force_login(user)
        session = client.session
        session["active_tenant_id"] = self.tenant_a.pk
        session.save()
        return client

    def test_get_requires_cross_tenant_permission(self):
        plain = User.objects.create_user(username="scope-plain", password="password123")
        response = self._client_for(plain).get(self.url)
        self.assertNotEqual(response.status_code, 200)

    def test_get_shows_scope_tenants_and_actions(self):
        response = self._client_for(self.admin).get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Approval Tenant A")
        self.assertContains(response, "Approval Tenant B")
        self.assertContains(response, "Approve Scope")
        self.assertNotContains(response, "Revoke Approval")

    def test_approve_post_creates_durable_authorization(self):
        response = self._client_for(self.admin).post(self.url, {"action": "approve"})
        self.assertEqual(response.status_code, 302)
        authorization = ScheduledReportScopeAuthorization.objects.get(scheduled_report=self.sched)
        self.assertEqual(authorization.authorized_by, self.admin)
        self.assertEqual(authorization.scope_tenant_ids, [self.tenant_a.pk, self.tenant_b.pk])
        self.assertIsNone(authorization.revoked_at)
        self.assertIsNone(authorization.revoked_by)

    def test_approve_post_refreshes_a_stale_scope(self):
        self._client_for(self.admin).post(self.url, {"action": "approve"})
        self.sched.filter_tenants.add(self.tenant_c)
        response = self._client_for(self.admin).post(self.url, {"action": "approve"})
        self.assertEqual(response.status_code, 302)
        authorization = ScheduledReportScopeAuthorization.objects.get(scheduled_report=self.sched)
        self.assertEqual(
            authorization.scope_tenant_ids,
            sorted([self.tenant_a.pk, self.tenant_b.pk, self.tenant_c.pk]),
        )

    def test_approve_post_refuses_when_reach_does_not_cover_scope(self):
        response = self._client_for(self.partial).post(self.url, {"action": "approve"}, follow=True)
        self.assertEqual(ScheduledReportScopeAuthorization.objects.filter(scheduled_report=self.sched).count(), 0)
        self.assertContains(response, "does not cover")

    def test_approve_of_single_tenant_schedule_is_rejected(self):
        self.sched.filter_tenants.clear()
        response = self._client_for(self.admin).post(self.url, {"action": "approve"}, follow=True)
        self.assertEqual(ScheduledReportScopeAuthorization.objects.filter(scheduled_report=self.sched).count(), 0)
        self.assertContains(response, "does not need cross-tenant scope approval")

    def test_revoke_post_marks_approval_revoked(self):
        self._client_for(self.admin).post(self.url, {"action": "approve"})
        response = self._client_for(self.admin).post(self.url, {"action": "revoke"})
        self.assertEqual(response.status_code, 302)
        authorization = ScheduledReportScopeAuthorization.objects.get(scheduled_report=self.sched)
        self.assertTrue(authorization.is_revoked())
        self.assertEqual(authorization.revoked_by, self.admin)
        self.assertIsNotNone(authorization.revoked_at)

    def test_revoke_post_refuses_when_reach_does_not_cover_the_stored_scope(self):
        self._client_for(self.admin).post(self.url, {"action": "approve"})
        response = self._client_for(self.partial).post(self.url, {"action": "revoke"}, follow=True)
        authorization = ScheduledReportScopeAuthorization.objects.get(scheduled_report=self.sched)
        self.assertFalse(authorization.is_revoked())
        self.assertContains(response, "does not cover")

    def test_approve_after_revoke_clears_the_revocation(self):
        client = self._client_for(self.admin)
        client.post(self.url, {"action": "approve"})
        client.post(self.url, {"action": "revoke"})
        client.post(self.url, {"action": "approve"})
        authorization = ScheduledReportScopeAuthorization.objects.get(scheduled_report=self.sched)
        self.assertFalse(authorization.is_revoked())
        self.assertIsNone(authorization.revoked_at)
        self.assertEqual(ScheduledReportScopeAuthorization.objects.filter(scheduled_report=self.sched).count(), 1)

    def test_revoke_without_approval_reports_an_error(self):
        response = self._client_for(self.admin).post(self.url, {"action": "revoke"}, follow=True)
        self.assertContains(response, "no cross-tenant scope approval to revoke")
        self.assertEqual(ScheduledReportScopeAuthorization.objects.filter(scheduled_report=self.sched).count(), 0)

    def test_unknown_action_reports_an_error_without_writing(self):
        response = self._client_for(self.admin).post(self.url, {"action": "nope"}, follow=True)
        self.assertContains(response, "Unknown scope approval action")
        self.assertEqual(ScheduledReportScopeAuthorization.objects.filter(scheduled_report=self.sched).count(), 0)

    def test_get_refuses_storing_an_ineffective_approval_state(self):
        response = self._client_for(self.partial).get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "would not take effect")

    def test_approve_refused_when_a_scope_tenant_does_not_resolve(self):
        through = self.sched.filter_tenants.through
        through._base_manager.create(scheduledreport_id=self.sched.pk, tenant_id=999999)
        response = self._client_for(self.admin).post(self.url, {"action": "approve"}, follow=True)
        self.assertEqual(ScheduledReportScopeAuthorization.objects.filter(scheduled_report=self.sched).count(), 0)
        self.assertContains(response, "does not resolve")

    def test_stored_authorizer_reach_loss_is_surfaced_on_the_page(self):
        from organization.models import RoleGrant

        self._client_for(self.operator).post(self.url, {"action": "approve"})
        RoleGrant.objects.filter(membership__user=self.operator, role__tenant=self.tenant_b).delete()
        response = self._client_for(self.admin).get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no longer takes effect")

    def test_off_host_return_url_is_sanitized(self):
        response = self._client_for(self.admin).post(
            self.url, {"action": "approve", "return_url": "https://evil.example/phish"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("extras:scheduledreport_list"))


class ScheduledReportScopeApprovalCapabilityGateTests(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Gate Tenant A", slug="gate-tenant-a")
        self.admin = User.objects.create_superuser(username="scope-gate-admin", password="password123", email="a@b.com")
        self.template = ReportTemplate.objects.create(
            name="Gate Template",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            tenant=self.tenant_a,
        )
        self.sched = ScheduledReport.objects.create(
            name="Gate Schedule",
            report=self.template,
            tenant=self.tenant_a,
            format=ScheduledReport.FORMAT_HTML,
            save_to_archive=False,
            is_active=True,
        )
        self.url = reverse("extras:scheduledreport_scope_approval", kwargs={"pk": self.sched.pk})

    @override_settings(REPORT_DESIGNER_ENABLED=False)
    def test_route_is_closed_when_the_designer_capability_is_inactive(self):
        client = Client()
        client.force_login(self.admin)
        response = client.get(self.url)
        self.assertEqual(response.status_code, 404)
