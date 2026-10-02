"""GraphQL journeys: the endpoint is read-only; every write goes through a service-backed REST/UI path."""

import json

import pytest
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from assets.models import Asset
from users.models import Token

from .support import JourneyMixin


class GraphQLReadOnlyJourneyTests(JourneyMixin, TestCase):
    def setUp(self):
        self.make_tenant("journey-graphql")
        self.writer = self.make_member(
            "graphql-writer", {"assets.add_asset", "assets.change_asset", "assets.delete_asset", "assets.view_asset"}
        )
        self.token = Token.objects.create(
            user=self.writer, tenant=self.tenant, expires=timezone.now() + timezone.timedelta(days=1)
        )

    def _post(self, query):
        return self.client.post(
            reverse("graphql"),
            data=json.dumps({"query": query}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )

    @pytest.mark.xfail(strict=True, reason="GraphQL still exposes mutations (#602)")
    def test_any_mutation_operation_is_rejected_even_for_a_fully_authorised_role(self):
        asset = self.make_asset(name="Journey GraphQL Asset")

        created = self._post('mutation { createAsset(name: "Journey GraphQL Created") { asset { id } } }')
        deleted = self._post("mutation { deleteAsset(id: %s) { success } }" % json.dumps(str(asset.pk)))

        for response in (created, deleted):
            self.assertIn("errors", response.json(), "a mutation operation was accepted")
        self.assertFalse(Asset.objects.filter(name="Journey GraphQL Created").exists())
        self.assertTrue(Asset.objects.filter(pk=asset.pk).exists())
