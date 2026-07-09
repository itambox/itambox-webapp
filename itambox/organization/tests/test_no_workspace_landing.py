"""Regression tests for the "no accessible workspace" login-state landing (§3-F, defect #7).

An authenticated non-superuser whose only membership was deactivated (or who was never
assigned one) keeps ``User.is_active``/``can_login`` True — the interactive UI does NOT
auto-clear them. Instead of dropping such a user into a permission-less, tenant-less
dashboard, ``DashboardView`` renders ``registration/no_workspace.html``. Superusers, and
users with any accessible tenant/provider, reach the normal dashboard.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.managers import set_current_tenant, set_current_membership
from core.tests.mixins import TenantTestMixin
from organization.models import Membership

User = get_user_model()


class NoWorkspaceLandingTests(TenantTestMixin, TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        # setup_tenant_context() builds: self.tenant, self.tenant_user (with an active
        # membership + role), and self.tenant_admin (a superuser).
        self.setup_tenant_context()
        self.dashboard_url = reverse('dashboard')

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def test_user_with_no_membership_hits_no_workspace_landing(self):
        """A logged-in non-superuser with zero memberships lands on the no-workspace page."""
        orphan = User.objects.create_user(
            username="orphan", email="orphan@example.com", password="password",
        )
        self.assertFalse(Membership.objects.filter(user=orphan).exists())

        self.client.force_login(orphan)
        response = self.client.get(self.dashboard_url)

        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, 'registration/no_workspace.html')
        self.assertContains(response, 'no-workspace-landing', status_code=403)

    def test_user_with_deactivated_last_membership_hits_no_workspace_landing(self):
        """Deactivating the user's last membership (interactive path) → no-workspace landing.

        The membership row and User.is_active remain — only the access gate flips.
        """
        self.tenant_membership.is_active = False
        self.tenant_membership.save(update_fields=['is_active'])
        self.tenant_user.refresh_from_db()
        self.assertTrue(self.tenant_user.is_active)  # global flag untouched

        self.client.force_login(self.tenant_user)
        response = self.client.get(self.dashboard_url)

        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, 'registration/no_workspace.html')

    def test_user_with_active_membership_reaches_dashboard(self):
        """A non-superuser with an active membership reaches the normal dashboard."""
        self.client.force_login(self.tenant_user)
        session = self.client.session
        session['active_tenant_id'] = self.tenant.pk
        session.save()

        response = self.client.get(self.dashboard_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'dashboard.html')
        self.assertTemplateNotUsed(response, 'registration/no_workspace.html')

    def test_superuser_with_no_membership_reaches_dashboard(self):
        """A superuser with no membership is exempt — they reach the normal dashboard."""
        self.assertFalse(Membership.objects.filter(user=self.tenant_admin).exists())

        self.client.force_login(self.tenant_admin)
        response = self.client.get(self.dashboard_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'dashboard.html')
        self.assertTemplateNotUsed(response, 'registration/no_workspace.html')
