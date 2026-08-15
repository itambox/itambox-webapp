"""Regression tests for issue #341 -- the REST API token lifecycle.

Before this fix: token creation returned HTTP 201 without the one-time
plaintext key, token detail responses carried no ETag even though mutations
required If-Match, and DELETE with a valid If-Match crashed with a 500
(TokenViewSet has no class-level `queryset`, and the shared lifecycle helpers
read `self.queryset.model` unconditionally).
"""

import logging
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import ObjectChange
from core.tests.mixins import TenantTestMixin
from itambox.api.mixins import ETagMixin
from users.api.serializers import TokenSerializer
from users.api.views import TokenViewSet
from users.models import Token

User = get_user_model()


class _CollectingHandler(logging.Handler):
    """Captures formatted log messages without requiring at least one record
    (unlike ``assertLogs``), so an all-clear (no plaintext) assertion is
    meaningful even on a code path that may legitimately log nothing."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage())


class TokenLifecycleAPITests(TenantTestMixin, APITestCase):
    def setUp(self):
        self.setup_tenant_context(
            name="Token Tenant",
            slug="token-tenant",
            permissions=[
                "users.add_token",
                "users.view_token",
                "users.change_token",
                "users.delete_token",
            ],
        )
        self.client.force_login(self.tenant_user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant.pk
        session.save()

    def _list_url(self):
        return reverse("api:users_api:token-list")

    def _detail_url(self, pk):
        return reverse("api:users_api:token-detail", kwargs={"pk": pk})

    def _create_token(self, **overrides):
        payload = {"user_id": self.tenant_user.pk, "description": "CI token"}
        payload.update(overrides)
        return self.client.post(self._list_url(), payload, format="json")

    def _current_etag(self, pk):
        return ETagMixin._get_etag(Token.objects.get(pk=pk))

    # --- AC1: one-time plaintext key on create ----------------------------

    def test_create_returns_plaintext_key_once_and_stores_only_digest(self):
        response = self._create_token()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        plaintext = response.data["key"]
        self.assertRegex(plaintext, r"^[0-9a-f]{40}$")

        token = Token.objects.get(pk=response.data["id"])
        self.assertNotEqual(token.digest, plaintext)
        self.assertTrue(token.digest)
        self.assertEqual(token.key_preview, plaintext[:8])

        # A follow-up detail read must never re-expose the plaintext.
        detail = self.client.get(self._detail_url(token.pk))
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertIsNone(detail.data["key"])

        # Nor must a list read.
        listing = self.client.get(self._list_url())
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertTrue(listing.data["results"])
        for row in listing.data["results"]:
            self.assertIsNone(row["key"])

    # --- AC2: token detail exposes a usable current ETag -------------------

    def test_get_detail_returns_usable_etag_header(self):
        create_response = self._create_token()
        pk = create_response.data["id"]

        response = self.client.get(self._detail_url(pk))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        etag = response["ETag"]
        self.assertEqual(etag, self._current_etag(pk))
        self.assertRegex(etag, r'^W/"[^"]+"$')

        # ...and it is actually usable as a mutation precondition.
        delete_response = self.client.delete(self._detail_url(pk), HTTP_IF_MATCH=etag)
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)

    def test_retrieve_without_change_tracking_omits_etag(self):
        # ETagMixin.retrieve() must degrade gracefully -- no header, no error --
        # for a model that carries neither `updated_at` nor `last_updated`
        # (the stock User model), rather than assuming every ETagMixin
        # viewset's model can produce one.
        self.client.force_login(self.tenant_admin)
        url = reverse("api:users_api:user-detail", kwargs={"pk": self.tenant_admin.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn("ETag", response)

    # --- AC3/AC4: DELETE precondition semantics -----------------------------

    def test_delete_with_current_etag_removes_token_and_makes_it_unusable(self):
        create_response = self._create_token()
        pk = create_response.data["id"]
        plaintext = create_response.data["key"]

        delete_response = self.client.delete(self._detail_url(pk), HTTP_IF_MATCH=self._current_etag(pk))
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)

        self.assertFalse(Token.objects.filter(pk=pk).exists())
        self.assertIsNone(Token.find_by_key(plaintext))

        follow_up = self.client.get(self._detail_url(pk))
        self.assertEqual(follow_up.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_without_if_match_returns_428_and_does_not_delete(self):
        create_response = self._create_token()
        pk = create_response.data["id"]

        response = self.client.delete(self._detail_url(pk))
        self.assertEqual(response.status_code, status.HTTP_428_PRECONDITION_REQUIRED)
        self.assertTrue(Token.objects.filter(pk=pk).exists())

    def test_delete_with_stale_if_match_returns_412_and_does_not_delete(self):
        create_response = self._create_token()
        pk = create_response.data["id"]

        response = self.client.delete(
            self._detail_url(pk),
            HTTP_IF_MATCH='W/"1999-01-01T00:00:00+00:00"',
        )
        self.assertEqual(response.status_code, status.HTTP_412_PRECONDITION_FAILED)
        self.assertTrue(Token.objects.filter(pk=pk).exists())

    # --- AC5: no 500 from a viewset with no class-level queryset ------------

    def test_tokenviewset_has_no_class_level_queryset(self):
        # This is the exact precondition that used to crash perform_destroy()
        # with AttributeError -> 500 (issue #341): TokenViewSet only defines
        # get_queryset(), never a class-level `queryset` attribute. The DELETE
        # tests above prove the crash is gone (204/428/412, never 500); this
        # pins down why it was possible to crash in the first place.
        self.assertIsNone(TokenViewSet.queryset)

    # --- AC6: no plaintext leakage in logs or the changelog -----------------

    def test_lifecycle_never_logs_plaintext_key(self):
        handler = _CollectingHandler()
        loggers = [logging.getLogger("itambox.api.views"), logging.getLogger("users.api.views")]
        for logger in loggers:
            logger.addHandler(handler)
        try:
            create_response = self._create_token()
            plaintext = create_response.data["key"]
            pk = create_response.data["id"]
            delete_response = self.client.delete(self._detail_url(pk), HTTP_IF_MATCH=self._current_etag(pk))
        finally:
            for logger in loggers:
                logger.removeHandler(handler)

        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertTrue(handler.records, "expected at least the perform_destroy log entry")
        for message in handler.records:
            self.assertNotIn(plaintext, message)

    def test_changelog_never_contains_plaintext(self):
        create_response = self._create_token()
        pk = create_response.data["id"]
        plaintext = create_response.data["key"]

        delete_response = self.client.delete(self._detail_url(pk), HTTP_IF_MATCH=self._current_etag(pk))
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)

        # _base_manager: independent of tenant scoping/soft-delete, mirrors the
        # established changelog-assertion pattern elsewhere in the suite.
        changes = ObjectChange._base_manager.filter(changed_object_id=pk, object_type_repr="users | token")
        self.assertTrue(changes.exists())
        for change in changes:
            self.assertNotIn(plaintext, str(change.prechange_data))
            self.assertNotIn(plaintext, str(change.postchange_data))
            self.assertNotIn(plaintext, change.object_repr)

    # --- AC (issue #353): owner transfer must never 500 after commit --------

    def test_superuser_owner_transfer_returns_200_and_persists(self):
        # A superuser may re-assign a token to another user. Before the fix the
        # transfer was committed but the response re-fetch failed against the
        # requester-scoped queryset (the owner moved out of scope) -> HTTP 500
        # after the commit, and a retry hit 404.
        token = Token.objects.create(user=self.tenant_admin, tenant=self.tenant)

        self.client.force_login(self.tenant_admin)
        session = self.client.session
        session["active_tenant_id"] = self.tenant.pk
        session.save()

        response = self.client.patch(
            self._detail_url(token.pk),
            {"user_id": self.tenant_user.pk},
            format="json",
            HTTP_IF_MATCH=self._current_etag(token.pk),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["user"], self.tenant_user.username)
        # The plaintext must never resurface on update.
        self.assertIsNone(response.data["key"])

        token.refresh_from_db()
        self.assertEqual(token.user_id, self.tenant_user.pk)

    def test_non_superuser_create_with_foreign_user_id_pins_owner_to_requester(self):
        response = self._create_token(user_id=self.tenant_admin.pk)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        token = Token.objects.get(pk=response.data["id"])
        self.assertEqual(token.user_id, self.tenant_user.pk)

    def test_non_superuser_update_with_foreign_user_id_keeps_owner(self):
        pk = self._create_token().data["id"]

        response = self.client.patch(
            self._detail_url(pk),
            {"user_id": self.tenant_admin.pk},
            format="json",
            HTTP_IF_MATCH=self._current_etag(pk),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["user"], self.tenant_user.username)
        self.assertEqual(Token.objects.get(pk=pk).user_id, self.tenant_user.pk)

    def test_create_without_active_tenant_fails_closed(self):
        # Under a tenant-group scope no single tenant anchors the request, but
        # permission checks aggregate over the group subtree, so the create
        # reaches the view. Before the fix Token.save() assigned the first
        # tenant in the database (fail-open). Issue #353.
        from organization.models import TenantGroup

        group = TenantGroup.objects.create(name="Token Group", slug="token-group")
        self.tenant.group = group
        self.tenant.save()

        session = self.client.session
        session.pop("active_tenant_id", None)
        session["active_tenant_group_id"] = group.pk
        session.save()

        response = self.client.post(
            self._list_url(),
            {"user_id": self.tenant_user.pk, "description": "unbound scope"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)
        self.assertFalse(Token._base_manager.filter(user=self.tenant_user).exists())

    def test_superuser_create_without_active_tenant_fails_closed(self):
        # The guard is unconditional: even a superuser cannot create a token
        # without an active tenant context — there is no "global" token, and
        # the old fallback would silently bind the first tenant in the DB.
        self.client.force_login(self.tenant_admin)
        session = self.client.session
        session.pop("active_tenant_id", None)
        session.pop("active_tenant_group_id", None)
        session.save()

        response = self.client.post(
            self._list_url(),
            {"user_id": self.tenant_user.pk, "description": "unbound superuser"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.data)
        self.assertFalse(Token._base_manager.filter(user=self.tenant_user).exists())

    # --- Shared-helper coverage: model derivation, bulk paths, refetch fallback --

    def test_viewset_model_derivation_covers_queryset_and_fallbacks(self):
        view = TokenViewSet()
        self.assertIs(view._get_model(serializer=TokenSerializer()), Token)
        self.assertIs(view._get_model(serializer=TokenSerializer(many=True)), Token)
        self.assertIs(view._get_model(instance=Token(user=self.tenant_user, tenant=self.tenant)), Token)

        view.queryset = Token.objects.all()
        self.assertIs(view._get_model(), Token)

        view.queryset = None
        with self.assertRaisesRegex(AssertionError, "queryset, serializer, or instance"):
            view._get_model()

    def test_bulk_partial_update_writes_through_the_shared_bulk_path(self):
        first = self._create_token(description="bulk one").data["id"]
        second = self._create_token(description="bulk two").data["id"]

        response = self.client.patch(
            self._list_url(),
            [
                {"id": first, "description": "bulk one updated"},
                {"id": second, "description": "bulk two updated"},
            ],
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(Token.objects.get(pk=first).description, "bulk one updated")
        self.assertEqual(Token.objects.get(pk=second).description, "bulk two updated")

    def test_bulk_destroy_writes_through_the_shared_bulk_path(self):
        first = Token.objects.get(pk=self._create_token().data["id"])
        second = Token.objects.get(pk=self._create_token().data["id"])
        # perform_destroy enforces the precondition contract per object, so the
        # bulk request must carry the current ETag of every target.
        etags = f"{ETagMixin._get_etag(first)}, {ETagMixin._get_etag(second)}"

        response = self.client.delete(
            self._list_url(),
            [{"id": first.pk}, {"id": second.pk}],
            format="json",
            HTTP_IF_MATCH=etags,
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Token.objects.filter(pk__in=[first.pk, second.pk]).exists())

    def test_created_response_instance_falls_back_when_refetch_misses(self):
        view = TokenViewSet()
        serializer = mock.Mock(many=False, instance=mock.Mock(pk=42))
        with mock.patch.object(TokenViewSet, "get_queryset", side_effect=ObjectDoesNotExist):
            result = view.get_created_response_instance(serializer)
        self.assertIs(result, serializer.instance)
