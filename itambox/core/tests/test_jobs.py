"""Tests for the Jobs UI: tenant scoping, cancel semantics, list metrics."""

from django.test import TestCase
from django.urls import reverse

from core.models import Job
from core.tests.mixins import TenantTestMixin
from organization.models import Tenant


class JobScopingTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(permissions=["core.view_job", "core.change_job"])
        self.other_tenant = Tenant.objects.create(name="Other Tenant", slug="other-tenant")
        self.own_job = Job.objects.create(name="own-tenant-job", tenant=self.tenant)
        self.foreign_job = Job.objects.create(name="foreign-tenant-job", tenant=self.other_tenant)
        self.system_job = Job.objects.create(name="system-job", tenant=None)

    def test_member_sees_only_own_tenant_jobs(self):
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        response = self.client.get(reverse("job_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "own-tenant-job")
        self.assertNotContains(response, "foreign-tenant-job")
        self.assertNotContains(response, "system-job")

    def test_member_cannot_open_foreign_job_detail(self):
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        response = self.client.get(reverse("job_detail", kwargs={"pk": self.foreign_job.pk}))
        self.assertEqual(response.status_code, 404)

    def test_member_cannot_cancel_foreign_job(self):
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        response = self.client.post(reverse("job_cancel", kwargs={"pk": self.foreign_job.pk}))
        self.assertEqual(response.status_code, 404)
        self.foreign_job.refresh_from_db()
        self.assertEqual(self.foreign_job.status, Job.STATUS_PENDING)

    def test_superuser_sees_all_jobs(self):
        self.client_login_to_tenant(self.tenant_admin, self.tenant)
        response = self.client.get(reverse("job_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "own-tenant-job")
        self.assertContains(response, "foreign-tenant-job")
        self.assertContains(response, "system-job")

    def test_list_context_metrics(self):
        Job.objects.create(name="running-job", tenant=self.tenant, status=Job.STATUS_RUNNING)
        Job.objects.create(name="done-job", tenant=self.tenant, status=Job.STATUS_COMPLETED)
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        response = self.client.get(reverse("job_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["status_counts"],
            {"pending": 1, "running": 1, "completed": 1, "failed": 0},
        )
        self.assertTrue(response.context["has_active_jobs"])

    # ── aggregate ("all accessible tenants") scope ──
    def _login_all_accessible(self):
        self.client.force_login(self.tenant_user)
        session = self.client.session
        session.pop("active_tenant_id", None)
        session["active_all_accessible"] = True
        session.save()

    def _grant_other_tenant(self):
        role = self.tenant_role.__class__.objects.create(
            tenant=self.other_tenant,
            name="Other Role",
            permissions=["core.view_job", "core.change_job"],
        )
        self.grant(self.tenant_user, self.other_tenant, role)

    def test_aggregate_scope_lists_jobs_of_accessible_tenants(self):
        """A bulk job bound to any accessible tenant stays visible in aggregate scope."""
        self._grant_other_tenant()
        self._login_all_accessible()
        response = self.client.get(reverse("job_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "own-tenant-job")
        self.assertContains(response, "foreign-tenant-job")
        self.assertNotContains(response, "system-job")

    def test_aggregate_scope_opens_accessible_job_detail(self):
        """The redirect after an aggregate bulk submit must land on a readable page."""
        self._grant_other_tenant()
        self._login_all_accessible()
        response = self.client.get(reverse("job_detail", kwargs={"pk": self.foreign_job.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "foreign-tenant-job")
        self.assertTrue(response.context["can_cancel_job"])
        self.assertContains(response, "Cancel job")

    def test_concrete_scope_shows_cancel_only_with_change_permission(self):
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        response = self.client.get(reverse("job_detail", kwargs={"pk": self.own_job.pk}))
        self.assertTrue(response.context["can_cancel_job"])
        self.assertContains(response, "Cancel job")

        self.tenant_role.permissions = ["core.view_job"]
        self.tenant_role.save()
        response = self.client.get(reverse("job_detail", kwargs={"pk": self.own_job.pk}))
        self.assertFalse(response.context["can_cancel_job"])
        self.assertNotContains(response, "Cancel job")

    def test_concrete_scope_hides_cancel_for_running_and_completed_jobs(self):
        running = Job.objects.create(name="running-detail-job", tenant=self.tenant, status=Job.STATUS_RUNNING)
        completed = Job.objects.create(name="completed-detail-job", tenant=self.tenant, status=Job.STATUS_COMPLETED)
        self.client_login_to_tenant(self.tenant_user, self.tenant)

        for job in (running, completed):
            with self.subTest(status=job.status):
                response = self.client.get(reverse("job_detail", kwargs={"pk": job.pk}))
                self.assertFalse(response.context["can_cancel_job"])
                self.assertNotContains(response, "Cancel job")

    def test_aggregate_scope_hides_cancel_without_target_tenant_change_permission(self):
        view_only_role = self.tenant_role.__class__.objects.create(
            tenant=self.other_tenant,
            name="Other View Only Role",
            permissions=["core.view_job"],
        )
        self.grant(self.tenant_user, self.other_tenant, view_only_role)
        self._login_all_accessible()

        response = self.client.get(reverse("job_detail", kwargs={"pk": self.foreign_job.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_cancel_job"])
        self.assertNotContains(response, "Cancel job")

    def test_aggregate_scope_hides_unreachable_tenant_job(self):
        """A job of a tenant the user cannot access stays hidden even in aggregate scope."""
        unreachable = Tenant.objects.create(name="Unreachable Tenant", slug="unreachable-tenant")
        hidden_job = Job.objects.create(name="unreachable-job", tenant=unreachable)
        self._login_all_accessible()
        response = self.client.get(reverse("job_list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "unreachable-job")
        response = self.client.get(reverse("job_detail", kwargs={"pk": hidden_job.pk}))
        self.assertEqual(response.status_code, 404)

    def test_aggregate_scope_can_cancel_accessible_job(self):
        self._grant_other_tenant()
        self._login_all_accessible()
        response = self.client.post(reverse("job_cancel", kwargs={"pk": self.foreign_job.pk}))
        self.assertRedirects(response, reverse("job_detail", kwargs={"pk": self.foreign_job.pk}))
        self.foreign_job.refresh_from_db()
        self.assertEqual(self.foreign_job.status, Job.STATUS_FAILED)


class JobCancelTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(permissions=["core.view_job", "core.change_job"])

    def test_cancel_pending_job(self):
        job = Job.objects.create(name="pending-job", tenant=self.tenant)
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        response = self.client.post(reverse("job_cancel", kwargs={"pk": job.pk}))
        self.assertRedirects(response, reverse("job_detail", kwargs={"pk": job.pk}))
        job.refresh_from_db()
        self.assertEqual(job.status, Job.STATUS_FAILED)
        self.assertIn("Cancelled by", job.logs)

    def test_cannot_cancel_running_job(self):
        job = Job.objects.create(name="running-job", tenant=self.tenant, status=Job.STATUS_RUNNING)
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        self.client.post(reverse("job_cancel", kwargs={"pk": job.pk}))
        job.refresh_from_db()
        self.assertEqual(job.status, Job.STATUS_RUNNING)

    def test_cancel_requires_change_permission(self):
        job = Job.objects.create(name="pending-job", tenant=self.tenant)
        viewer = self.tenant_user
        # Replace role perms with view-only
        self.tenant_role.permissions = ["core.view_job"]
        self.tenant_role.save()
        self.client_login_to_tenant(viewer, self.tenant)
        response = self.client.post(reverse("job_cancel", kwargs={"pk": job.pk}))
        self.assertRedirects(response, reverse("job_list"))
        job.refresh_from_db()
        self.assertEqual(job.status, Job.STATUS_PENDING)

    def test_mark_running_refuses_cancelled_job(self):
        """A worker picking up a cancelled job must not execute it."""
        job = Job.objects.create(name="cancelled-job", tenant=self.tenant)
        self.assertTrue(job.cancel("cancelled in test"))
        self.assertFalse(job.mark_running())
        job.refresh_from_db()
        self.assertEqual(job.status, Job.STATUS_FAILED)

    def test_cancel_refuses_started_job(self):
        job = Job.objects.create(name="started-job", tenant=self.tenant)
        self.assertTrue(job.mark_running())
        self.assertFalse(job.cancel("too late"))
        job.refresh_from_db()
        self.assertEqual(job.status, Job.STATUS_RUNNING)
