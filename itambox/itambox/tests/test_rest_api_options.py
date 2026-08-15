from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from assets.models import Manufacturer

User = get_user_model()


class RestCollectionOptionsTests(APITestCase):
    collection_routes = (
        ("Assets", "api:assets_api:manufacturer-list"),
        ("Inventory", "api:inventory_api:accessory-list"),
        ("Organization", "api:organization_api:site-list"),
        ("Procurement", "api:procurement_api:contract-list"),
        ("Software", "api:software_api:software-list"),
        ("Subscriptions", "api:subscriptions_api:provider-list"),
        ("Users", "api:users_api:token-list"),
    )

    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="options-superuser",
            email="options-superuser@example.com",
            password="password123",
        )

    def test_authenticated_options_returns_metadata_for_collection_routes(self):
        self.client.force_authenticate(user=self.superuser)

        for domain, route_name in self.collection_routes:
            with self.subTest(domain=domain, route_name=route_name):
                response = self.client.options(reverse(route_name))

                self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
                self.assertIn("name", response.data)
                self.assertIn("description", response.data)

    def test_unauthenticated_options_returns_401_for_collection_routes(self):
        for domain, route_name in self.collection_routes:
            with self.subTest(domain=domain, route_name=route_name):
                response = self.client.options(reverse(route_name))

                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED, response.data)

    def test_detail_get_still_resolves_object_with_real_pk(self):
        manufacturer = Manufacturer.objects.create(
            name="OPTIONS Detail Manufacturer",
            slug="options-detail-manufacturer",
        )
        self.client.force_authenticate(user=self.superuser)

        response = self.client.get(reverse("api:assets_api:manufacturer-detail", kwargs={"pk": manufacturer.pk}))

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["id"], manufacturer.pk)
