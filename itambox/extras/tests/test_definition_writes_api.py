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

CHOICE_SET_URL = "/api/extras/custom-field-choice-sets/"
CHOICE_URL = "/api/extras/custom-field-choices/"


class ChoiceSetWritesAPITests(TestCase):
    """REST transport for the choice-set and choice definition commands."""

    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("choice-admin", "choice@example.com", "pw")
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def _create_set(self, slug="api-choice-set", choices=(("alpha", "Alpha"), ("beta", "Beta"))):
        return self.client.post(
            CHOICE_SET_URL,
            {
                "namespace": "local",
                "slug": slug,
                "label": "API choice set",
                "choices": [{"key": key, "label": label} for key, label in choices],
            },
            format="json",
        )

    def _choice_ids(self, slug="api-choice-set"):
        created = self._create_set(slug=slug)
        return created, [choice["id"] for choice in created.data["choices"]]

    def test_create_choice_set_with_choices_returns_201_and_etag(self):
        response = self._create_set()
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(response["ETag"])
        self.assertEqual([choice["key"] for choice in response.data["choices"]], ["alpha", "beta"])
        self.assertTrue(response.data["resource_revision"])

    def test_create_choice_set_initialises_positions_in_order(self):
        response = self._create_set()
        self.assertEqual([choice["position"] for choice in response.data["choices"]], [1, 2])

    def test_create_choice_set_with_duplicate_keys_is_refused(self):
        response = self._create_set(choices=(("dup", "One"), ("dup", "Two")))
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.data["error"]["issues"][0]["code"], "DUPLICATE_FIELD")

    def test_create_choice_set_rejects_conflicting_identity(self):
        self._create_set()
        response = self._create_set()
        self.assertIn(response.status_code, (400, 409), response.content)
        self.assertIn("error", response.data)

    def test_choice_set_detail_exposes_etag_and_choices(self):
        created, choice_ids = self._choice_ids()
        response = self.client.get(f"{CHOICE_SET_URL}{created.data['id']}/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response["ETag"])
        self.assertEqual(sorted(choice["id"] for choice in response.data["choices"]), sorted(choice_ids))

    def test_choice_detail_exposes_etag(self):
        _, choice_ids = self._choice_ids()
        response = self.client.get(f"{CHOICE_URL}{choice_ids[0]}/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response["ETag"])
        self.assertEqual(response.data["id"], choice_ids[0])

    def test_choice_set_patch_requires_if_match(self):
        created, _ = self._choice_ids()
        response = self.client.patch(f"{CHOICE_SET_URL}{created.data['id']}/", {"label": "Renamed"}, format="json")
        self.assertEqual(response.status_code, 428, response.content)

    def test_choice_set_patch_updates_label_and_rotates_etag(self):
        created, _ = self._choice_ids()
        response = self.client.patch(
            f"{CHOICE_SET_URL}{created.data['id']}/",
            {"label": "Renamed"},
            format="json",
            HTTP_IF_MATCH=created["ETag"],
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["label"], "Renamed")
        self.assertNotEqual(response["ETag"], created["ETag"])

    def test_choice_set_patch_with_stale_etag_is_refused_and_unchanged(self):
        created, _ = self._choice_ids()
        self.client.patch(
            f"{CHOICE_SET_URL}{created.data['id']}/",
            {"label": "First"},
            format="json",
            HTTP_IF_MATCH=created["ETag"],
        )
        response = self.client.patch(
            f"{CHOICE_SET_URL}{created.data['id']}/",
            {"label": "Second"},
            format="json",
            HTTP_IF_MATCH=created["ETag"],
        )
        self.assertIn(response.status_code, (409, 412), response.content)
        detail = self.client.get(f"{CHOICE_SET_URL}{created.data['id']}/")
        self.assertEqual(detail.data["label"], "First")

    def test_choice_set_deprecate_requires_if_match_then_deprecates(self):
        created, _ = self._choice_ids()
        missing = self.client.post(f"{CHOICE_SET_URL}{created.data['id']}/deprecate/", {}, format="json")
        self.assertEqual(missing.status_code, 428, missing.content)
        response = self.client.post(
            f"{CHOICE_SET_URL}{created.data['id']}/deprecate/",
            {},
            format="json",
            HTTP_IF_MATCH=created["ETag"],
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotEqual(response.data["lifecycle"], created.data["lifecycle"])

    def test_add_choice_requires_if_match_then_appends(self):
        created, choice_ids = self._choice_ids()
        missing = self.client.post(
            f"{CHOICE_SET_URL}{created.data['id']}/choices/", {"key": "gamma", "label": "Gamma"}, format="json"
        )
        self.assertEqual(missing.status_code, 428, missing.content)
        response = self.client.post(
            f"{CHOICE_SET_URL}{created.data['id']}/choices/",
            {"key": "gamma", "label": "Gamma"},
            format="json",
            HTTP_IF_MATCH=created["ETag"],
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data["key"], "gamma")
        self.assertEqual(response.data["position"], len(choice_ids) + 1)
        self.assertTrue(response["ETag"])

    def test_add_choice_with_stale_revision_is_refused(self):
        created, _ = self._choice_ids()
        self.client.post(
            f"{CHOICE_SET_URL}{created.data['id']}/choices/",
            {"key": "gamma", "label": "Gamma"},
            format="json",
            HTTP_IF_MATCH=created["ETag"],
        )
        response = self.client.post(
            f"{CHOICE_SET_URL}{created.data['id']}/choices/",
            {"key": "delta", "label": "Delta"},
            format="json",
            HTTP_IF_MATCH=created["ETag"],
        )
        self.assertIn(response.status_code, (409, 412), response.content)

    def test_add_choice_with_duplicate_key_is_refused(self):
        created, _ = self._choice_ids()
        response = self.client.post(
            f"{CHOICE_SET_URL}{created.data['id']}/choices/",
            {"key": "alpha", "label": "Alpha again"},
            format="json",
            HTTP_IF_MATCH=created["ETag"],
        )
        self.assertIn(response.status_code, (400, 409), response.content)
        self.assertIn(
            response.data["error"]["issues"][0]["code"], {"DUPLICATE_FIELD", "REFERENCE_CONFLICT"}
        )

    def test_reorder_requires_if_match(self):
        created, _ = self._choice_ids()
        response = self.client.post(
            f"{CHOICE_SET_URL}{created.data['id']}/reorder/", {"keys": ["beta", "alpha"]}, format="json"
        )
        self.assertEqual(response.status_code, 428, response.content)

    def test_reorder_applies_the_requested_order(self):
        created, _ = self._choice_ids()
        response = self.client.post(
            f"{CHOICE_SET_URL}{created.data['id']}/reorder/",
            {"keys": ["beta", "alpha"]},
            format="json",
            HTTP_IF_MATCH=created["ETag"],
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([choice["key"] for choice in response.data["choices"]], ["beta", "alpha"])

    def test_reorder_rejects_duplicate_keys(self):
        created, _ = self._choice_ids()
        response = self.client.post(
            f"{CHOICE_SET_URL}{created.data['id']}/reorder/",
            {"keys": ["alpha", "alpha"]},
            format="json",
            HTTP_IF_MATCH=created["ETag"],
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.data["error"]["issues"][0]["code"], "DUPLICATE_FIELD")

    def test_reorder_rejects_non_permutation(self):
        created, _ = self._choice_ids()
        response = self.client.post(
            f"{CHOICE_SET_URL}{created.data['id']}/reorder/",
            {"keys": ["alpha", "gamma"]},
            format="json",
            HTTP_IF_MATCH=created["ETag"],
        )
        self.assertIn(response.status_code, (400, 409), response.content)
        self.assertIn("error", response.data)

    def test_reorder_with_stale_revision_is_refused(self):
        created, _ = self._choice_ids()
        self.client.post(
            f"{CHOICE_SET_URL}{created.data['id']}/reorder/",
            {"keys": ["beta", "alpha"]},
            format="json",
            HTTP_IF_MATCH=created["ETag"],
        )
        response = self.client.post(
            f"{CHOICE_SET_URL}{created.data['id']}/reorder/",
            {"keys": ["alpha", "beta"]},
            format="json",
            HTTP_IF_MATCH=created["ETag"],
        )
        self.assertIn(response.status_code, (409, 412), response.content)

    def test_choice_patch_requires_if_match_then_updates_label(self):
        _, choice_ids = self._choice_ids()
        missing = self.client.patch(f"{CHOICE_URL}{choice_ids[0]}/", {"label": "New"}, format="json")
        self.assertEqual(missing.status_code, 428, missing.content)
        created = self.client.get(f"{CHOICE_URL}{choice_ids[0]}/")
        response = self.client.patch(
            f"{CHOICE_URL}{choice_ids[0]}/", {"label": "New"}, format="json", HTTP_IF_MATCH=created["ETag"]
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["label"], "New")

    def test_choice_patch_with_stale_etag_is_refused(self):
        _, choice_ids = self._choice_ids()
        current = self.client.get(f"{CHOICE_URL}{choice_ids[0]}/")
        self.client.patch(
            f"{CHOICE_URL}{choice_ids[0]}/", {"label": "First"}, format="json", HTTP_IF_MATCH=current["ETag"]
        )
        response = self.client.patch(
            f"{CHOICE_URL}{choice_ids[0]}/", {"label": "Second"}, format="json", HTTP_IF_MATCH=current["ETag"]
        )
        self.assertIn(response.status_code, (409, 412), response.content)

    def test_choice_deprecate_requires_if_match_then_deprecates(self):
        _, choice_ids = self._choice_ids()
        missing = self.client.post(f"{CHOICE_URL}{choice_ids[0]}/deprecate/", {}, format="json")
        self.assertEqual(missing.status_code, 428, missing.content)
        current = self.client.get(f"{CHOICE_URL}{choice_ids[0]}/")
        response = self.client.post(
            f"{CHOICE_URL}{choice_ids[0]}/deprecate/", {}, format="json", HTTP_IF_MATCH=current["ETag"]
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotEqual(response.data["lifecycle"], current.data["lifecycle"])

    def test_unauthenticated_write_is_denied(self):
        client = APIClient()
        response = client.post(CHOICE_SET_URL, {"namespace": "local", "slug": "anon"}, format="json")
        self.assertIn(response.status_code, (401, 403), response.content)

    def test_user_without_provider_catalogue_permission_is_denied(self):
        user = get_user_model().objects.create_user("choice-plain", "choice-plain@example.com", "pw")
        client = APIClient()
        client.force_authenticate(user)
        response = client.post(CHOICE_SET_URL, {"namespace": "local", "slug": "nope"}, format="json")
        self.assertEqual(response.status_code, 403, response.content)

    def test_write_permission_mapping_follows_the_action(self):
        from extras.api.choice_sets import DefinitionActionPermissions
        from extras.models import CustomFieldChoice, CustomFieldChoiceSet

        permissions = DefinitionActionPermissions()

        class _View:
            def __init__(self, action):
                self.action = action

        cases = {
            "create": "extras.add_customfieldchoiceset",
            "destroy": "extras.delete_customfieldchoiceset",
            "choices": "extras.add_customfieldchoice",
            "partial_update": "extras.change_customfieldchoiceset",
        }
        for action, expected in cases.items():
            with self.subTest(action=action):
                mapped = permissions._write_permissions(_View(action), CustomFieldChoiceSet)
                self.assertEqual(mapped, [expected])
        mapped = permissions._write_permissions(_View("partial_update"), CustomFieldChoice)
        self.assertEqual(mapped, ["extras.change_customfieldchoice"])


class CommandWriteMixinSurfaceTests(TestCase):
    """The command-backed viewsets expose reads with ETags and no full-replace write."""

    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("mixsurface-admin", "mixsurface@example.com", "pw")
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_custom_field_detail_exposes_etag(self):
        created = self.client.post(
            FIELD_URL,
            {"namespace": "local", "local_key": "etag_field", "label": "ETag field", "object_types": ["assets.asset"]},
            format="json",
        )
        response = self.client.get(f"{FIELD_URL}{created.data['id']}/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response["ETag"])

    def test_custom_fieldset_detail_exposes_etag(self):
        created = self.client.post(
            FIELDSET_URL, {"namespace": "local", "slug": "etag-set", "label": "ETag set"}, format="json"
        )
        response = self.client.get(f"{FIELDSET_URL}{created.data['id']}/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response["ETag"])
