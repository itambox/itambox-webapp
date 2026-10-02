"""Catalogue journeys: catalogue permissions granted through a role are honoured by the views."""

import pytest
from django.test import TestCase
from django.urls import reverse

from assets.models import AssetType, Manufacturer

from .support import JourneyMixin


class CataloguePermissionJourneyTests(JourneyMixin, TestCase):
    def setUp(self):
        self.make_tenant("journey-catalogue")

    @pytest.mark.xfail(strict=True, reason="library views ignore role grants (#611)")
    def test_role_with_view_specificationlibrary_can_open_type_libraries(self):
        reader = self.make_member("library-reader", {"extras.view_specificationlibrary"})
        self.client_login_to_tenant(reader, self.tenant)

        response = self.client.get(reverse("assets:type_library_list"))

        self.assertEqual(response.status_code, 200)

    @pytest.mark.xfail(strict=True, reason="asset type creation ignores role grants (#611)")
    def test_role_with_add_assettype_can_create_an_asset_type(self):
        manufacturer = Manufacturer.objects.create(name="Journey Catalogue Maker", slug="journey-catalogue-maker")
        creator = self.make_member("type-creator", {"assets.add_assettype", "assets.view_assettype"})
        self.client_login_to_tenant(creator, self.tenant)

        response = self.client.post(
            reverse("assets:assettype_create"),
            {"manufacturer": manufacturer.pk, "model": "Journey Type", "slug": "journey-type"},
        )

        self.assertIn(response.status_code, (200, 302))
        self.assertTrue(AssetType.objects.filter(model="Journey Type").exists())
