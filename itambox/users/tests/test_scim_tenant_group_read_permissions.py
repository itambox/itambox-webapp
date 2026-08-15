from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from core.tests.mixins import grant
from organization.models import Role, Tenant
from users.models import Token, UserGroup

User = get_user_model()


class SCIMTenantGroupReadPermissionTests(TestCase):
    error_schema = "urn:ietf:params:scim:api:messages:2.0:Error"

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Acme Corp", slug="acme")
        self.other_tenant = Tenant.objects.create(name="Other Corp", slug="other")

        role_without_view = Role.objects.create(
            tenant=self.tenant,
            name="SCIM identity only",
            permissions=["organization.change_membership"],
        )
        role_with_view = Role.objects.create(
            tenant=self.tenant,
            name="SCIM group reader",
            permissions=["organization.change_membership", "users.view_usergroup"],
        )

        user_without_view = User.objects.create_user(username="scim-no-group-read")
        user_with_view = User.objects.create_user(username="scim-group-reader")
        grant(user_without_view, self.tenant, role_without_view)
        grant(user_with_view, self.tenant, role_with_view)

        token_without_view = Token.objects.create(
            user=user_without_view,
            tenant=self.tenant,
            expires=timezone.now() + timezone.timedelta(days=1),
        )
        token_with_view = Token.objects.create(
            user=user_with_view,
            tenant=self.tenant,
            expires=timezone.now() + timezone.timedelta(days=1),
        )
        self.headers_without_view = {"HTTP_AUTHORIZATION": f"Bearer {token_without_view.key}"}
        self.headers_with_view = {"HTTP_AUTHORIZATION": f"Bearer {token_with_view.key}"}

        self.group = UserGroup.objects.create(tenant=self.tenant, name="Tenant Group")
        self.other_group = UserGroup.objects.create(tenant=self.other_tenant, name="Other Tenant Group")
        self.list_url = reverse("api:scim:group-list", kwargs={"tenant_slug": self.tenant.slug})
        self.detail_url = reverse(
            "api:scim:group-detail",
            kwargs={"tenant_slug": self.tenant.slug, "pk": self.group.pk},
        )
        self.other_detail_url = reverse(
            "api:scim:group-detail",
            kwargs={"tenant_slug": self.tenant.slug, "pk": self.other_group.pk},
        )

    def assert_scim_error(self, response, expected_status):
        self.assertEqual(response.status_code, expected_status)
        data = response.json()
        self.assertEqual(data["schemas"], [self.error_schema])
        self.assertEqual(data["status"], str(expected_status))
        self.assertIn("detail", data)

    def test_group_reads_without_view_permission_return_scim_403(self):
        for url in (self.list_url, self.detail_url):
            with self.subTest(url=url):
                response = self.client.get(url, **self.headers_without_view)
                self.assert_scim_error(response, status.HTTP_403_FORBIDDEN)

    def test_group_reads_with_view_permission_succeed(self):
        list_response = self.client.get(self.list_url, **self.headers_with_view)
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(list_response.json()["totalResults"], 1)
        self.assertEqual(list_response.json()["Resources"][0]["displayName"], self.group.name)

        detail_response = self.client.get(self.detail_url, **self.headers_with_view)
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_response.json()["displayName"], self.group.name)

    def test_group_writes_stay_forbidden_with_view_permission(self):
        cases = [
            (
                "post",
                self.list_url,
                {
                    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
                    "displayName": "Created via SCIM",
                },
            ),
            (
                "put",
                self.detail_url,
                {
                    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
                    "displayName": "Renamed via SCIM",
                },
            ),
            (
                "patch",
                self.detail_url,
                {
                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                    "Operations": [],
                },
            ),
            ("delete", self.detail_url, None),
        ]

        for method, url, payload in cases:
            with self.subTest(method=method):
                client_method = getattr(self.client, method)
                if payload is None:
                    response = client_method(url, **self.headers_with_view)
                else:
                    response = client_method(
                        url,
                        data=payload,
                        content_type="application/json",
                        **self.headers_with_view,
                    )
                self.assert_scim_error(response, status.HTTP_403_FORBIDDEN)

        self.group.refresh_from_db()
        self.assertEqual(self.group.name, "Tenant Group")
        self.assertFalse(UserGroup.objects.filter(tenant=self.tenant, name="Created via SCIM").exists())

    def test_other_tenant_group_is_never_exposed(self):
        cases = [
            ("without permission", self.headers_without_view, status.HTTP_403_FORBIDDEN),
            ("with permission", self.headers_with_view, status.HTTP_404_NOT_FOUND),
        ]

        for permission_state, headers, expected_status in cases:
            with self.subTest(permission_state=permission_state):
                detail_response = self.client.get(self.other_detail_url, **headers)
                self.assert_scim_error(detail_response, expected_status)
                self.assertNotIn(self.other_group.name, str(detail_response.json()))

                list_response = self.client.get(self.list_url, **headers)
                self.assertNotIn(self.other_group.name, str(list_response.json()))
                if expected_status == status.HTTP_403_FORBIDDEN:
                    self.assert_scim_error(list_response, expected_status)
                else:
                    self.assertEqual(list_response.status_code, status.HTTP_200_OK)
                    self.assertEqual(list_response.json()["totalResults"], 1)

