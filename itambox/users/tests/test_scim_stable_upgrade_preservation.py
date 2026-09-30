"""Upgrade preservation for the SCIM Provisioning Stable promotion (epic #571).

The promotion is registry-only: it adds no migration and must not rewrite,
reinterpret, re-create, or delete anything an operator or identity provider
created on the supported Beta (through ``1.0.0-beta.3``). Every preserved
``User.scim_id``, ``Membership.external_id`` mapping, ``UserGroup``
``scim_id``/``external_id`` pair, ``GroupMembership`` provenance row and SCIM
token keeps its exact value and meaning, and the mounts answer the first
post-upgrade requests with the same identity data as before - including
correlation paths, filtering, and least-privilege token behavior.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from core.tests.mixins import grant
from itambox.capabilities import registry
from organization.models import Membership, Role, Tenant
from users.models import GroupMembership, Token, UserGroup

User = get_user_model()


class SCIMStableUpgradePreservationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Upgrade SCIM", slug="upgrade-scim")
        self.provider = Tenant.objects.create(name="Upgrade MSP", slug="upgrade-msp", is_provider=True)
        self.role = Role.objects.create(
            tenant=self.tenant,
            name="Upgrade provisioner",
            permissions=["organization.change_membership", "users.view_usergroup"],
        )
        self.provider_role = Role.objects.create(
            tenant=self.provider,
            name="Upgrade provider provisioner",
            permissions=[
                "organization.change_membership",
                "users.view_usergroup",
                "users.add_usergroup",
                "users.change_usergroup",
                "users.delete_usergroup",
            ],
        )
        self.actor = User.objects.create_user(username="upgrade-actor")
        grant(self.actor, self.tenant, self.role)
        self.provider_actor = User.objects.create_user(username="upgrade-provider-actor")
        grant(self.provider_actor, self.provider, self.provider_role)

        # Beta-era state as an IdP would have provisioned it before the promotion.
        self.user = User.objects.create_user(username="beta.user@corp.example", email="beta.user@corp.example")
        self.membership = Membership.objects.create(
            user=self.user,
            tenant=self.tenant,
            is_active=True,
            external_id="beta-entra-object-0001",
        )
        self.group = UserGroup.objects.create(tenant=self.tenant, name="Beta group", external_id="beta-group-ext")
        self.group_membership = GroupMembership.objects.create(
            user_group=self.group,
            membership=self.membership,
            source=GroupMembership.SOURCE_SCIM,
            external_id=str(self.user.scim_id),
            added_by=self.actor,
        )
        self.token = Token.objects.create(
            user=self.actor,
            tenant=self.tenant,
            expires=timezone.now() + timezone.timedelta(days=1),
        )
        self.auth_headers = {"HTTP_AUTHORIZATION": f"Bearer {self.token.key}"}

    @staticmethod
    def _snapshot(row, *, exclude=()):
        return {
            field.attname: getattr(row, field.attname)
            for field in row._meta.concrete_fields
            if field.attname not in exclude
        }

    def test_preserved_rows_are_byte_identical_through_first_operations(self):
        """The first post-upgrade requests must not rewrite, re-create or renumber
        anything: replaying the IdP's own POST and reading the mounts leaves every
        preserved row exactly as the Beta left it."""
        rows = (self.user, self.membership, self.group, self.group_membership)
        before = {row.pk: self._snapshot(row) for row in rows}
        token_before = self._snapshot(self.token, exclude=("last_used",))

        list_url = reverse("api:scim:user-list", kwargs={"tenant_slug": self.tenant.slug})
        payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "externalId": "beta-entra-object-0001",
            "userName": "beta.user@corp.example",
            "name": {"givenName": "Beta", "familyName": "User"},
            "emails": [{"value": "beta.user@corp.example", "type": "work"}],
            "active": True,
        }
        replay = self.client.post(list_url, data=payload, content_type="application/json", **self.auth_headers)
        self.assertEqual(replay.status_code, status.HTTP_200_OK)
        self.assertEqual(replay.json()["id"], str(self.user.scim_id))

        detail_url = reverse(
            "api:scim:user-detail", kwargs={"tenant_slug": self.tenant.slug, "pk": str(self.user.scim_id)}
        )
        self.assertEqual(self.client.get(detail_url, **self.auth_headers).status_code, status.HTTP_200_OK)
        self.client.get(list_url, **self.auth_headers)
        self.client.get(reverse("api:scim:group-list", kwargs={"tenant_slug": self.tenant.slug}), **self.auth_headers)

        for row in rows:
            row.refresh_from_db()
            self.assertEqual(self._snapshot(row), before[row.pk])
        self.token.refresh_from_db()
        self.assertEqual(self._snapshot(self.token, exclude=("last_used",)), token_before)
        self.assertEqual(GroupMembership.objects.filter(user_group=self.group).count(), 1)

    def test_mappings_stay_continuous_and_usable_across_the_promotion(self):
        """Preserved identifiers keep resolving: the old scim_id addresses the same
        resource, the old external_id still filters and correlates, and re-provisioning
        returns the same opaque id (no re-numbering)."""
        list_url = reverse("api:scim:user-list", kwargs={"tenant_slug": self.tenant.slug})
        filtered = self.client.get(f'{list_url}?filter=externalId eq "beta-entra-object-0001"', **self.auth_headers)
        self.assertEqual(filtered.status_code, status.HTTP_200_OK)
        resources = filtered.json()["Resources"]
        self.assertEqual([row["id"] for row in resources], [str(self.user.scim_id)])

        group_url = reverse(
            "api:scim:group-detail", kwargs={"tenant_slug": self.tenant.slug, "pk": str(self.group.scim_id)}
        )
        group_response = self.client.get(group_url, **self.auth_headers)
        self.assertEqual(group_response.status_code, status.HTTP_200_OK)
        self.assertEqual(group_response.json()["id"], str(self.group.scim_id))
        member_ids = {member["value"] for member in group_response.json().get("members", [])}
        self.assertIn(str(self.user.scim_id), member_ids)

        # Provider mount rows stay addressable the same way.
        self.provider_group = UserGroup.objects.create(
            tenant=self.provider, name="Beta provider group", external_id="beta-provider-group-ext"
        )
        provider_group_url = reverse(
            "api:provider_scim:group-detail",
            kwargs={"provider_slug": self.provider.slug, "pk": str(self.provider_group.scim_id)},
        )
        provider_token = Token.objects.create(
            user=self.provider_actor,
            tenant=self.provider,
            expires=timezone.now() + timezone.timedelta(days=1),
        )
        provider_response = self.client.get(provider_group_url, HTTP_AUTHORIZATION=f"Bearer {provider_token.key}")
        self.assertEqual(provider_response.status_code, status.HTTP_200_OK)
        self.assertEqual(provider_response.json()["id"], str(self.provider_group.scim_id))
        self.assertEqual(provider_response.json()["externalId"], "beta-provider-group-ext")

    def test_promotion_does_not_auto_provision_and_keeps_least_privilege(self):
        """Being Stable must not provision anything by itself, and an existing
        read-only token stays read-only (no least-privilege change)."""
        state = registry.state("users.scim_provisioning")
        self.assertTrue(state.active)

        # Nothing appears implicitly: the Beta rows are the only rows
        # (tenant actor + provider actor + the provisioned user).
        self.assertEqual(Membership.objects.count(), 3)
        self.assertEqual(UserGroup.objects.count(), 1)
        self.assertEqual(GroupMembership.objects.count(), 1)

        read_only = Token.objects.create(
            user=self.actor,
            tenant=self.tenant,
            write_enabled=False,
            expires=timezone.now() + timezone.timedelta(days=1),
        )
        headers = {"HTTP_AUTHORIZATION": f"Bearer {read_only.key}"}
        list_url = reverse("api:scim:user-list", kwargs={"tenant_slug": self.tenant.slug})
        self.assertEqual(self.client.get(list_url, **headers).status_code, status.HTTP_200_OK)
        response = self.client.post(
            list_url,
            data={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": "must-not-exist@corp.example"},
            content_type="application/json",
            **headers,
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(User.objects.filter(username="must-not-exist@corp.example").exists())

        # Reading still created nothing new.
        self.assertEqual(Membership.objects.count(), 3)
        self.assertEqual(UserGroup.objects.count(), 1)
        self.assertEqual(GroupMembership.objects.count(), 1)
