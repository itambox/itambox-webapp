"""Localized UI surfaces for Purchase Orders (Issue #261).

German and English render contracts for navigation, breadcrumbs, page
heading, and empty-state output on the Purchase Order list surface.
The raw Django default verbose name ("purchase orders") must never leak
as a user-facing label.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.tests.mixins import grant
from organization.models import Role, Tenant
from procurement.models import PurchaseOrder

User = get_user_model()


class PurchaseOrderLocalizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="POL Tenant", slug="pol-tenant")
        cls.user = User.objects.create_user(username="pol-localization", password="x")
        role = Role.objects.create(
            tenant=cls.tenant,
            name="PO localization reader",
            permissions=["procurement.view_purchaseorder"],
        )
        grant(cls.user, cls.tenant, role)

    def _login(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant.pk
        session.save()

    def _list_response(self, language):
        self._login()
        # LocaleMiddleware derives the active language from the request headers,
        # overriding any thread-level translation.override() during rendering.
        return self.client.get(reverse("procurement:purchaseorder_list"), HTTP_ACCEPT_LANGUAGE=language)

    def test_german_list_uses_localized_labels(self):
        response = self._list_response("de")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bestellungen")
        # Raw Django default / model identifier must not leak as a label.
        self.assertNotContains(response, ">purchase orders<")
        self.assertNotContains(response, "purchase orders")

    def test_english_list_uses_catalog_capitalization(self):
        response = self._list_response("en")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Purchase Orders")
        self.assertNotContains(response, ">purchase orders<")

    def test_german_empty_state_is_fully_localized(self):
        self.assertFalse(PurchaseOrder.objects.exists())
        response = self._list_response("de")
        self.assertContains(response, "Keine Bestellungen gefunden")
        self.assertNotContains(response, "purchase orders")

    def test_english_empty_state_is_fully_localized(self):
        self.assertFalse(PurchaseOrder.objects.exists())
        response = self._list_response("en")
        self.assertContains(response, "No Purchase Orders found")
        self.assertNotContains(response, "Keine")
