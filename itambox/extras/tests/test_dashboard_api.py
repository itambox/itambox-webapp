from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.tests.mixins import grant
from extras.models import Dashboard
from organization.models import Role, Tenant

User = get_user_model()


class DashboardAPITests(APITestCase):
    """Dashboard API creation must enforce authentication, permissions, and ownership."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Dashboard API Tenant", slug="dashboard-api-tenant")
        self.dashboard_role = Role.objects.create(
            tenant=self.tenant,
            name="Dashboard API User",
            permissions=["extras.view_dashboard", "extras.add_dashboard"],
        )
        self.no_permission_role = Role.objects.create(
            tenant=self.tenant,
            name="Dashboard API No Access",
            permissions=[],
        )

        self.dashboard_user = User.objects.create_user(username="dashboard_api_user", password="pw")
        grant(self.dashboard_user, self.tenant, self.dashboard_role)

        self.no_permission_user = User.objects.create_user(username="dashboard_api_denied", password="pw")
        grant(self.no_permission_user, self.tenant, self.no_permission_role)

        self.superuser = User.objects.create_superuser(username="dashboard_api_admin", password="pw")
        self.list_url = reverse("api:extras_api:dashboard-list")

    def _login_as(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant.pk
        session.save()

    def test_valid_create_is_retrievable_and_owned_by_requester(self):
        """A valid dashboard is created, returned by detail, and owned by its requester."""
        self._login_as(self.superuser)

        response = self.client.post(self.list_url, {"layout": []}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        dashboard = Dashboard.objects.get(pk=response.data["id"])
        self.assertEqual(dashboard.user, self.superuser)
        self.assertEqual(dashboard.layout, [])

        detail_url = reverse("api:extras_api:dashboard-detail", kwargs={"pk": dashboard.pk})
        detail_response = self.client.get(detail_url)
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK, detail_response.data)
        self.assertEqual(detail_response.data["id"], dashboard.pk)
        self.assertEqual(detail_response.data["user"], str(self.superuser))

    def test_invalid_layout_returns_structured_bad_request(self):
        """A non-list layout is rejected with a field-specific 400 response."""
        self._login_as(self.dashboard_user)

        response = self.client.post(self.list_url, {"layout": "not-a-list"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertIn("layout", response.data)
        self.assertIsInstance(response.data["layout"], list)
        self.assertFalse(Dashboard.objects.filter(user=self.dashboard_user).exists())

    def test_unauthenticated_create_returns_unauthorized(self):
        """Anonymous callers cannot create dashboards."""
        response = self.client.post(self.list_url, {"layout": []}, format="json")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED, response.data)

    def test_authenticated_user_without_permission_returns_forbidden(self):
        """An active tenant membership alone does not grant dashboard creation."""
        self._login_as(self.no_permission_user)

        response = self.client.post(self.list_url, {"layout": []}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)
        self.assertFalse(Dashboard.objects.filter(user=self.no_permission_user).exists())

    def test_non_superuser_with_permission_can_create(self):
        """Permission checks support a viewset whose model comes from get_queryset()."""
        self.assertFalse(self.dashboard_user.is_superuser)
        self._login_as(self.dashboard_user)

        response = self.client.post(self.list_url, {"layout": []}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertTrue(Dashboard.objects.filter(pk=response.data["id"], user=self.dashboard_user).exists())
