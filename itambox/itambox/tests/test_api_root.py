from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.urls import NoReverseMatch, get_resolver, resolve, reverse
from django.urls.resolvers import URLResolver
from rest_framework import status
from rest_framework.response import Response
from rest_framework.test import APITestCase

User = get_user_model()

# The namespaces the API root is specified to advertise, plus the non-namespace
# "status" link added by the root view itself. This is the independent
# expectation for the discovery tests: it documents the public contract and
# catches drift in either the root view or the URL configuration.
ADVERTISED_NAMESPACES = frozenset(
    {
        "assets",
        "compliance",
        "core",
        "extras",
        "inventory",
        "licenses",
        "organization",
        "procurement",
        "software",
        "subscriptions",
        "users",
    }
)

NON_DISCOVERABLE_KEYS = ("scim", "provider_scim", "schema", "auth-check")


class APIRootDiscoveryTests(APITestCase):
    """The API root must advertise every mounted, discoverable top-level API namespace."""

    def setUp(self) -> None:
        self.superuser = User.objects.create_superuser(
            username="api-root-superuser",
            email="api-root-superuser@example.com",
            password="password123",
        )

    def _get_root(self) -> Response:
        self.client.force_authenticate(user=self.superuser)
        return self.client.get(reverse("api:api-root"))

    @staticmethod
    def _mounted_discoverable_namespaces() -> set[str]:
        """Namespaces the live URL resolver mounts under /api/ with a root view.

        Read from the route table itself, so the advertised keys can be
        compared against the actually mounted namespaces in both directions.
        """
        _, api_resolver = get_resolver().namespace_dict["api"]

        namespaces: set[str] = set()
        for pattern in api_resolver.url_patterns:
            if not isinstance(pattern, URLResolver) or pattern.namespace is None:
                continue
            try:
                reverse(f"api:{pattern.namespace}:api-root")
            except NoReverseMatch:
                continue
            namespaces.add(str(pattern.pattern).rstrip("/"))
        return namespaces

    def test_unauthenticated_request_is_rejected(self) -> None:
        response = self.client.get(reverse("api:api-root"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_root_includes_compliance_inventory_procurement(self) -> None:
        response = self._get_root()

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        for key in ("compliance", "inventory", "procurement"):
            self.assertIn(key, response.data)

    def test_root_advertises_exactly_the_intended_namespaces(self) -> None:
        response = self._get_root()

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(set(response.data), set(ADVERTISED_NAMESPACES) | {"status"})

    def test_advertised_keys_match_mounted_namespaces_both_ways(self) -> None:
        """Every advertised key maps to a mounted namespace with a root view and vice versa."""
        response = self._get_root()

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        advertised = set(response.data) - {"status"}
        self.assertEqual(advertised, self._mounted_discoverable_namespaces())

    def test_non_discoverable_mounts_are_not_advertised(self) -> None:
        response = self._get_root()

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        for key in NON_DISCOVERABLE_KEYS:
            self.assertNotIn(key, response.data)

    def test_every_advertised_url_is_unique_and_resolves(self) -> None:
        response = self._get_root()

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        urls = list(response.data.values())
        self.assertEqual(len(urls), len(set(urls)), "Root links must be unique")
        for name, url in response.data.items():
            with self.subTest(name=name):
                # Raises Resolver404 if the advertised URL is not a valid route.
                resolve(urlparse(url).path)
