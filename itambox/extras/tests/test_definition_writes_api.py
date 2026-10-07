from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from extras.models import CustomField, CustomFieldset

FIELD_URL = "/api/extras/custom-fields/"
FIELDSET_URL = "/api/extras/custom-fieldsets/"


class DefinitionWritesAPITests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("defwrite-admin", "defwrite@example.com", "pw")
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def _create_field(self, key="api_field"):
        return self.client.post(
            FIELD_URL,
            {"namespace": "local", "local_key": key, "label": "API field", "object_types": ["assets.asset"]},
            format="json",
        )

    def test_create_field_returns_201_with_etag(self):
        response = self._create_field()
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(response["ETag"])
        self.assertTrue(CustomField.objects.filter(pk=response.data["id"]).exists())

    def test_create_rejects_unknown_member(self):
        response = self.client.post(
            FIELD_URL,
            {"namespace": "local", "local_key": "x", "label": "x", "object_types": ["assets.asset"], "bogus": 1},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_patch_requires_if_match(self):
        pk = self._create_field().data["id"]
        response = self.client.patch(f"{FIELD_URL}{pk}/", {"label": "New"}, format="json")
        self.assertEqual(response.status_code, 428, response.content)

    def test_patch_with_etag_updates_and_rotates_etag(self):
        created = self._create_field()
        pk = created.data["id"]
        response = self.client.patch(
            f"{FIELD_URL}{pk}/", {"label": "Renamed"}, format="json", HTTP_IF_MATCH=created["ETag"]
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(CustomField.objects.get(pk=pk).label, "Renamed")
        self.assertNotEqual(response["ETag"], created["ETag"])

    def test_patch_with_stale_etag_is_refused_and_row_unchanged(self):
        created = self._create_field()
        pk = created.data["id"]
        self.client.patch(f"{FIELD_URL}{pk}/", {"label": "First"}, format="json", HTTP_IF_MATCH=created["ETag"])
        response = self.client.patch(
            f"{FIELD_URL}{pk}/", {"label": "Second"}, format="json", HTTP_IF_MATCH=created["ETag"]
        )
        self.assertIn(response.status_code, (409, 412), response.content)
        self.assertEqual(CustomField.objects.get(pk=pk).label, "First")

    def test_put_is_not_allowed(self):
        pk = self._create_field().data["id"]
        response = self.client.put(f"{FIELD_URL}{pk}/", {"label": "x"}, format="json")
        self.assertEqual(response.status_code, 405)

    def test_non_superuser_without_permission_is_denied(self):
        user = get_user_model().objects.create_user("defwrite-plain", "plain@example.com", "pw")
        client = APIClient()
        client.force_authenticate(user)
        response = client.post(
            FIELD_URL,
            {"namespace": "local", "local_key": "nope", "label": "x", "object_types": ["assets.asset"]},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_fieldset_create_and_patch(self):
        created = self.client.post(
            FIELDSET_URL, {"namespace": "local", "slug": "api-set", "label": "Set"}, format="json"
        )
        self.assertEqual(created.status_code, 201, created.content)
        pk = created.data["id"]
        response = self.client.patch(
            f"{FIELDSET_URL}{pk}/", {"label": "Set 2"}, format="json", HTTP_IF_MATCH=created["ETag"]
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(CustomFieldset.objects.get(pk=pk).label, "Set 2")
