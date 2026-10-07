"""Catalogue journeys: role grants reach the catalogue views under the approved scope.

Catalogue rows -- asset types, their specifications, and type libraries -- are
global, so the approved provider-scoped semantics (#611, implemented by #646)
authorize them against a concrete, active provider tenant only. A role grant
held in a plain customer tenant (or in a tenant group, or under the
"All accessible tenants" scope) never opens a global catalogue surface, while a
role grant held in a managing (``is_provider``) tenant does.
"""

from django.test import TestCase
from django.urls import reverse

from assets.models import AssetType, Manufacturer

from .support import JourneyMixin


class CataloguePermissionJourneyTests(JourneyMixin, TestCase):
    def setUp(self):
        self.customer = self.make_tenant("journey-catalogue")
        self.provider = self.make_provider_tenant("journey-catalogue-provider")

    def test_provider_role_with_view_specificationlibrary_can_open_type_libraries(self):
        reader = self.make_member("library-reader", {"extras.view_specificationlibrary"}, tenant=self.provider)
        self.client_login_to_tenant(reader, self.provider)

        response = self.client.get(reverse("assets:type_library_list"))

        self.assertEqual(response.status_code, 200)

    def test_customer_role_with_view_specificationlibrary_cannot_open_type_libraries(self):
        reader = self.make_member("customer-library-reader", {"extras.view_specificationlibrary"}, tenant=self.customer)
        self.client_login_to_tenant(reader, self.customer)

        response = self.client.get(reverse("assets:type_library_list"))

        self.assertEqual(response.status_code, 403)

    def test_provider_role_with_add_assettype_can_create_an_asset_type(self):
        manufacturer = Manufacturer.objects.create(name="Journey Catalogue Maker", slug="journey-catalogue-maker")
        creator = self.make_member(
            "type-creator", {"assets.add_assettype", "assets.view_assettype"}, tenant=self.provider
        )
        self.client_login_to_tenant(creator, self.provider)

        response = self.client.post(
            reverse("assets:assettype_create"),
            {"manufacturer": manufacturer.pk, "model": "Journey Type", "slug": "journey-type"},
        )

        self.assertIn(response.status_code, (200, 302))
        self.assertTrue(AssetType.objects.filter(model="Journey Type").exists())

    def test_customer_role_with_add_assettype_cannot_create_an_asset_type(self):
        manufacturer = Manufacturer.objects.create(name="Journey Customer Maker", slug="journey-customer-maker")
        creator = self.make_member(
            "customer-type-creator", {"assets.add_assettype", "assets.view_assettype"}, tenant=self.customer
        )
        self.client_login_to_tenant(creator, self.customer)

        response = self.client.post(
            reverse("assets:assettype_create"),
            {"manufacturer": manufacturer.pk, "model": "Journey Customer Type", "slug": "journey-customer-type"},
        )

        self.assertNotEqual(response.status_code, 302)
        self.assertFalse(AssetType.objects.filter(model="Journey Customer Type").exists())
