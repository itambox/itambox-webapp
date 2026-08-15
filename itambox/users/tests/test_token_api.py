import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from model_bakery import baker
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from core.models import ObjectChange
from core.tests.mixins import grant
from organization.models import Role, Tenant
from users.api.serializers import TokenSerializer
from users.api.views import TokenViewSet
from users.models import Token

User = get_user_model()


class TokenAPILifecycleTests(APITestCase):
    def setUp(self):
        self.tenant = baker.make(Tenant, name="Token API tenant", slug="token-api-tenant")
        self.user = User.objects.create_user(username="token_api_user", password="password123")
        role = baker.make(
            Role,
            tenant=self.tenant,
            name="Token API role",
            permissions=[
                "users.add_token",
                "users.change_token",
                "users.delete_token",
                "users.view_token",
            ],
        )
        grant(self.user, self.tenant, role)
        self.client.force_login(self.user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant.pk
        session.save()
        self.list_url = reverse("api:users_api:token-list")

    def _create_token(self):
        response = self.client.post(
            self.list_url,
            {
                "description": "Issue 341 integration token",
                "user_id": self.user.pk,
                "write_enabled": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return response

    @staticmethod
    def _serialized_changes(token_id):
        token_type = ContentType.objects.get_for_model(Token)
        changes = ObjectChange._base_manager.filter(
            changed_object_type=token_type,
            changed_object_id=token_id,
        ).values("object_repr", "prechange_data", "postchange_data")
        return json.dumps(list(changes), sort_keys=True, default=str)

    def test_create_returns_plaintext_once_and_detail_has_current_etag(self):
        response = self._create_token()
        plaintext = response.data["key"]
        self.assertRegex(plaintext, r"^[0-9a-f]{40}$")

        token = Token.objects.get(pk=response.data["id"])
        self.assertEqual(token.tenant, self.tenant)
        self.assertIsNone(token.key)
        self.assertEqual(token.key_preview, plaintext[:8])
        self.assertNotEqual(token.digest, plaintext)
        self.assertRegex(token.digest, r"^[0-9a-f]{64}$")

        detail_url = reverse("api:users_api:token-detail", kwargs={"pk": token.pk})
        detail = self.client.get(detail_url)

        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail["ETag"], f'W/"{token.updated_at.isoformat()}"')
        self.assertIsNone(detail.data["key"])
        self.assertNotIn(plaintext, detail.content.decode())

        listing = self.client.get(self.list_url)
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        listed_token = next(row for row in listing.data["results"] if row["id"] == token.pk)
        self.assertIsNone(listed_token["key"])
        self.assertNotIn(plaintext, listing.content.decode())
        self.assertNotIn(plaintext, self._serialized_changes(token.pk))
        self.assertNotIn(token.digest, self._serialized_changes(token.pk))

    def test_update_advances_the_token_etag(self):
        create = self._create_token()
        plaintext = create.data["key"]
        token_id = create.data["id"]
        detail_url = reverse("api:users_api:token-detail", kwargs={"pk": token_id})
        original_etag = self.client.get(detail_url)["ETag"]

        updated = self.client.patch(
            detail_url,
            {"description": "Updated issue 341 token"},
            format="json",
            HTTP_IF_MATCH=original_etag,
        )

        self.assertEqual(updated.status_code, status.HTTP_200_OK, updated.data)
        self.assertNotEqual(updated["ETag"], original_etag)
        self.assertIsNone(updated.data["key"])
        self.assertNotIn(plaintext, updated.content.decode())
        self.assertEqual(self.client.get(detail_url)["ETag"], updated["ETag"])
        self.assertEqual(Token.objects.get(pk=token_id).description, "Updated issue 341 token")

    def test_delete_with_current_etag_revokes_token_without_leaking_plaintext(self):
        with self.assertLogs("itambox.api.views", level="INFO") as captured:
            create = self._create_token()
            plaintext = create.data["key"]
            token_id = create.data["id"]
            digest = Token.objects.get(pk=token_id).digest
            detail_url = reverse("api:users_api:token-detail", kwargs={"pk": token_id})
            etag = self.client.get(detail_url)["ETag"]
            deleted = self.client.delete(detail_url, HTTP_IF_MATCH=etag)

        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Token.objects.filter(pk=token_id).exists())
        self.assertEqual(self.client.get(detail_url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertIsNone(Token.find_by_key(plaintext))

        credential_client = APIClient()
        rejected = credential_client.get(self.list_url, HTTP_AUTHORIZATION=f"Token {plaintext}")
        self.assertEqual(rejected.status_code, status.HTTP_401_UNAUTHORIZED)

        log_output = "\n".join(captured.output)
        self.assertNotIn(plaintext, log_output)
        self.assertNotIn(digest, log_output)
        changes = self._serialized_changes(token_id)
        self.assertNotIn(plaintext, changes)
        self.assertNotIn(digest, changes)

    def test_delete_requires_current_etag_without_writing(self):
        create = self._create_token()
        token = Token.objects.get(pk=create.data["id"])
        detail_url = reverse("api:users_api:token-detail", kwargs={"pk": token.pk})
        current_etag = self.client.get(detail_url)["ETag"]
        stale_etag = f'W/"{(token.updated_at - timedelta(seconds=1)).isoformat()}"'
        token_type = ContentType.objects.get_for_model(Token)
        changes = ObjectChange._base_manager.filter(
            changed_object_type=token_type,
            changed_object_id=token.pk,
        )
        initial_change_count = changes.count()

        missing = self.client.delete(detail_url)
        self.assertEqual(missing.status_code, status.HTTP_428_PRECONDITION_REQUIRED)
        self.assertEqual(missing.data["detail"], "If-Match header is required for mutating requests.")
        self.assertTrue(Token.objects.filter(pk=token.pk).exists())
        self.assertEqual(changes.count(), initial_change_count)

        stale = self.client.delete(detail_url, HTTP_IF_MATCH=stale_etag)
        self.assertEqual(stale.status_code, status.HTTP_412_PRECONDITION_FAILED)
        self.assertNotEqual(stale_etag, current_etag)
        self.assertTrue(Token.objects.filter(pk=token.pk).exists())
        self.assertEqual(changes.count(), initial_change_count)

    def test_viewset_model_derivation_covers_queryset_and_fallbacks(self):
        view = TokenViewSet()
        self.assertIs(view._get_model(serializer=TokenSerializer()), Token)
        self.assertIs(view._get_model(serializer=TokenSerializer(many=True)), Token)
        self.assertIs(view._get_model(instance=Token(user=self.user, tenant=self.tenant)), Token)

        view.queryset = Token.objects.all()
        self.assertIs(view._get_model(), Token)

        view.queryset = None
        with self.assertRaisesRegex(AssertionError, "queryset, serializer, or instance"):
            view._get_model()
