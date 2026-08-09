"""Localized UI surfaces for Tenant Resource Grants (Issue #261).

German and English render contracts for navigation, breadcrumbs, page
heading, and empty-state output on the resource grant list surface.
Raw English/model labels ("Resource Grants", "Tenant resource grants")
must not leak in the German UI.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from organization.models import Tenant

User = get_user_model()


class ResourceGrantLocalizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="RGL Tenant", slug="rgl-tenant")

    def _login(self, permissions=("organization.view_tenantresourcegrant",)):
        user = User.objects.create_user(username="rgl-localization", password="x")
        from core.tests.mixins import grant

        from organization.models import Role

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
