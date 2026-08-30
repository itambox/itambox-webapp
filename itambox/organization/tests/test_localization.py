"""Localized UI surfaces for Tenant Resource Grants (Issue #261).

German and English render contracts for navigation, breadcrumbs, page
heading, and empty-state output on the resource grant list surface.
Raw English/model labels ("Resource Grants", "Tenant resource grants")
must not leak in the German UI.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from core.tests.mixins import TenantTestMixin, grant
from organization.filters import RegionFilterSet
from organization.models import Role, Tenant

User = get_user_model()


class OrganizationFilterPresentationTests(TestCase):
    def test_clear_filter_link_preserves_the_django_request_path_variable(self):
        filterset = RegionFilterSet()
        clear_link = next(
            field.html
            for field in filterset.form.helper.layout.fields
            if hasattr(field, "html") and "Clear filters" in field.html
        )

        self.assertIn("{{ request.path }}", clear_link)
        self.assertNotIn("{ request.path }", clear_link)


class ResourceGrantLocalizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="RGL Tenant", slug="rgl-tenant")

    def _login(self, permissions=("organization.view_tenantresourcegrant",)):
        user = User.objects.create_user(username="rgl-localization", password="x")
        role = Role.objects.create(
            tenant=self.tenant,
            name="RGL localization reader",
            permissions=list(permissions),
        )
        grant(user, self.tenant, role)
        self.client.force_login(user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant.pk
        session.save()
        return user

    def _list_response(self, language):
        self._login()
        # LocaleMiddleware derives the active language from the request headers,
        # overriding any thread-level translation.override() during rendering.
        # The override context restores the previous thread language afterwards,
        # so the request language cannot leak into later tests in this worker.
        with translation.override(language):
            return self.client.get(
                reverse("organization:tenantresourcegrant_list"),
                HTTP_ACCEPT_LANGUAGE=language,
            )

    def test_german_list_uses_localized_labels(self):
        response = self._list_response("de")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ressourcen-Freigaben")
        # Raw English labels must not leak in the German UI.
        self.assertNotContains(response, "Resource Grants")
        self.assertNotContains(response, "Tenant resource grants")

    def test_english_list_uses_catalog_capitalization(self):
        response = self._list_response("en")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Resource Grants")
        self.assertNotContains(response, "Tenant resource grants")

    def test_german_empty_state_is_fully_localized(self):
        response = self._list_response("de")
        self.assertContains(response, "Keine Ressourcen-Freigaben gefunden")
        self.assertNotContains(response, "Resource Grants")
        self.assertNotContains(response, "Tenant resource grants")

    def test_english_empty_state_is_fully_localized(self):
        response = self._list_response("en")
        self.assertContains(response, "No Resource Grants found")
        self.assertNotContains(response, "Keine")


class RoleSurfaceLocalizationTests(TenantTestMixin, TestCase):
    """DE/EN render contracts for the role form, role assign-users, and
    membership form surfaces (issue #386 organization slice).

    The tightened English source copy must render in EN, the reviewed German
    catalog entries in DE, and the old wordy source strings must not leak in
    either language.
    """

    def setUp(self):
        self.clear_tenant_context()
        self.setup_tenant_context()
        self.client.force_login(self.tenant_admin)
        session = self.client.session
        session["active_tenant_id"] = self.tenant.pk
        session.save()

    def tearDown(self):
        self.clear_tenant_context()

    def _get(self, url, language):
        with translation.override(language):
            return self.client.get(url, HTTP_ACCEPT_LANGUAGE=language)

    def test_english_role_form_uses_tightened_copy(self):
        response = self._get(reverse("organization:role_create"), "en")
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Choose which actions this role allows. Create and edit can be assigned separately.",
        )
        self.assertNotContains(response, "Configure role-specific access")
        self.assertContains(response, "A role belongs to the tenant it was created in and cannot be moved.")
        self.assertNotContains(response, "it is created in and cannot be moved afterwards")
        self.assertContains(response, "Read-only (no changes)")
        self.assertNotContains(response, "Read-Only (view only)")

    def test_german_role_form_uses_localized_copy(self):
        response = self._get(reverse("organization:role_create"), "de")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Wählen Sie aus, welche Aktionen diese Rolle erlaubt")
        self.assertNotContains(response, "Konfigurieren Sie den rollenspezifischen Zugriff")
        self.assertNotContains(response, "Set this role's permissions below")

    def test_english_role_assign_users_uses_tightened_copy(self):
        role = Role.objects.create(tenant=self.tenant, name="Assign target role")
        response = self._get(reverse("organization:role_assign_users", kwargs={"pk": role.pk}), "en")
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f"Select users to assign to this role in tenant <strong>{self.tenant.name}</strong>.",
        )
        self.assertNotContains(response, "with a different role will be reassigned")

    def test_german_role_assign_users_is_localized(self):
        role = Role.objects.create(tenant=self.tenant, name="Assign target role")
        response = self._get(reverse("organization:role_assign_users", kwargs={"pk": role.pk}), "de")
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f"Wählen Sie Benutzer aus, die Sie dieser Rolle im Mandanten <strong>{self.tenant.name}</strong> zuweisen möchten.",
        )
        self.assertNotContains(response, "Select users to assign to this role in tenant")
        self.assertNotContains(response, "mit einer anderen Rolle vorhanden sind")

    def test_english_membership_form_uses_tightened_copy(self):
        response = self._get(reverse("organization:membership_create"), "en")
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Choose who to add: select an existing user or create a new user by email.",
        )
        self.assertNotContains(response, "Choose an existing user or create one by email.")
