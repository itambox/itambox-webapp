from django.contrib.auth import get_user_model
from django.urls import NoReverseMatch, get_resolver, reverse
from django.urls.resolvers import URLResolver
from rest_framework import status
from rest_framework.test import APITestCase

User = get_user_model()


class APINamespaceRootTests(APITestCase):
    """Every mounted API namespace root must serve its discovery links, never a 500 (issue #363)."""

    def setUp(self) -> None:
        self.superuser = User.objects.create_superuser(
            username="namespace-root-superuser",
            email="namespace-root-superuser@example.com",
            password="password123",
        )

    @staticmethod
    def _mounted_discoverable_namespaces() -> list[tuple[str, str]]:
        """(url_prefix, namespace) for every /api/ namespace that has a root view."""
        _, api_resolver = get_resolver().namespace_dict["api"]

        namespaces: list[tuple[str, str]] = []
        for pattern in api_resolver.url_patterns:
            if not isinstance(pattern, URLResolver) or pattern.namespace is None:
                continue
            try:
                reverse(f"api:{pattern.namespace}:api-root")
            except NoReverseMatch:
                continue
            namespaces.append((str(pattern.pattern).rstrip("/"), pattern.namespace))
        return namespaces

    def test_every_namespace_root_serves_an_authenticated_superuser(self) -> None:
        self.client.force_authenticate(user=self.superuser)

        namespaces = self._mounted_discoverable_namespaces()
        self.assertGreaterEqual(len(namespaces), 10, "sanity: the full namespace set must be mounted")

        for prefix, namespace in namespaces:
            with self.subTest(namespace=prefix):
                response = self.client.get(reverse(f"api:{namespace}:api-root"))
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertIsInstance(response.data, dict)
                self.assertTrue(response.data, "root must advertise at least one endpoint")

    def test_every_namespace_root_rejects_anonymous_requests(self) -> None:
        for prefix, namespace in self._mounted_discoverable_namespaces():
            with self.subTest(namespace=prefix):
                response = self.client.get(reverse(f"api:{namespace}:api-root"))
                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_software_root_advertises_registered_prefixes(self) -> None:
        self.client.force_authenticate(user=self.superuser)

        response = self.client.get(reverse("api:software_api:api-root"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("software", response.data)
        self.assertIn("installed-software", response.data)
