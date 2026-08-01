from uuid import UUID

from django.contrib.auth import get_user_model
from django.http import Http404
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from core.tests.mixins import grant
from organization.models import Membership, Role, Tenant
from users.api.scim.identifiers import identifier_lookup
from users.models import GroupMembership, Token, UserGroup

User = get_user_model()


class SCIMIdentityContractTests(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="identity-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="identity-b")
        self.provider = Tenant.objects.create(name="Provider", slug="identity-provider", is_provider=True)

        self.tenant_role = Role.objects.create(
            tenant=self.tenant_a,
            name="SCIM provisioner",
            permissions=["organization.change_membership"],
        )
        self.tenant_b_role = Role.objects.create(
            tenant=self.tenant_b,
            name="SCIM provisioner B",
            permissions=["organization.change_membership"],
        )
        self.provider_role = Role.objects.create(
            tenant=self.provider,
            name="Provider SCIM provisioner",
            permissions=[
                "organization.change_membership",
                "users.view_usergroup",
                "users.add_usergroup",
                "users.change_usergroup",
                "users.delete_usergroup",
            ],
        )

        self.tenant_actor = User.objects.create_user(username="tenant-actor")
        self.tenant_b_actor = User.objects.create_user(username="tenant-b-actor")
        self.provider_actor = User.objects.create_user(username="provider-actor")
        grant(self.tenant_actor, self.tenant_a, self.tenant_role)
        grant(self.tenant_b_actor, self.tenant_b, self.tenant_b_role)
        grant(self.provider_actor, self.provider, self.provider_role)

        self.tenant_token = Token.objects.create(
            user=self.tenant_actor,
            tenant=self.tenant_a,
            expires=timezone.now() + timezone.timedelta(days=1),
        )
        self.tenant_b_token = Token.objects.create(
            user=self.tenant_b_actor,
            tenant=self.tenant_b,
            expires=timezone.now() + timezone.timedelta(days=1),
        )
        self.provider_token = Token.objects.create(
            user=self.provider_actor,
            tenant=self.provider,
            expires=timezone.now() + timezone.timedelta(days=1),
        )

    @staticmethod
    def headers(token):
        return {"HTTP_AUTHORIZATION": f"Bearer {token.key}"}

    def test_user_and_group_receive_stable_opaque_ids(self):
        user = User.objects.create_user(username="opaque-user")
        group = UserGroup.objects.create(tenant=self.tenant_a, name="Opaque group")

        user_scim_id = user.scim_id
        group_scim_id = group.scim_id
        self.assertIsInstance(user_scim_id, UUID)
        self.assertIsInstance(group_scim_id, UUID)
        self.assertNotEqual(user_scim_id, group_scim_id)
        user.refresh_from_db()
        group.refresh_from_db()
        self.assertEqual(user.scim_id, user_scim_id)
        self.assertEqual(group.scim_id, group_scim_id)

    def test_tenant_user_detail_dual_reads_but_emits_only_opaque_id(self):
        user = User.objects.create_user(username="tenant-user")
        membership = grant(user, self.tenant_a, self.tenant_role).membership
        membership.external_id = "tenant-directory-user"
        membership.save(update_fields=["external_id"])

        for identifier in (str(user.pk), str(user.scim_id)):
            url = reverse("api:scim:user-detail", kwargs={"tenant_slug": self.tenant_a.slug, "pk": identifier})
            response = self.client.get(url, **self.headers(self.tenant_token))
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            data = response.json()
            self.assertEqual(data["id"], str(user.scim_id))
            self.assertNotEqual(data["id"], str(user.pk))
            self.assertEqual(data["externalId"], "tenant-directory-user")
            self.assertTrue(data["meta"]["location"].endswith(f"/Users/{user.scim_id}"))

    def test_tenant_group_detail_and_member_refs_emit_opaque_ids(self):
        user = User.objects.create_user(username="group-user")
        membership = grant(user, self.tenant_a, self.tenant_role).membership
        group = UserGroup.objects.create(tenant=self.tenant_a, name="Tenant group", external_id="group-a")
        GroupMembership.objects.create(
            user_group=group,
            membership=membership,
            source=GroupMembership.SOURCE_SCIM,
            external_id=str(user.scim_id),
        )

        for identifier in (str(group.pk), str(group.scim_id)):
            url = reverse("api:scim:group-detail", kwargs={"tenant_slug": self.tenant_a.slug, "pk": identifier})
            response = self.client.get(url, **self.headers(self.tenant_token))
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            data = response.json()
            self.assertEqual(data["id"], str(group.scim_id))
            self.assertEqual(data["externalId"], "group-a")
            self.assertEqual(data["members"][0]["value"], str(user.scim_id))
            self.assertTrue(data["meta"]["location"].endswith(f"/Groups/{group.scim_id}"))
            self.assertTrue(data["members"][0]["$ref"].endswith(f"/Users/{user.scim_id}"))

    def test_user_external_id_is_scoped_to_requesting_tenant(self):
        user = User.objects.create_user(username="shared-user")
        membership_a = grant(user, self.tenant_a, self.tenant_role).membership
        membership_a.external_id = "true"
        membership_a.save(update_fields=["external_id"])
        membership_b = grant(user, self.tenant_b, self.tenant_b_role).membership
        membership_b.external_id = "directory-b"
        membership_b.save(update_fields=["external_id"])

        for tenant, token, external_id in (
            (self.tenant_a, self.tenant_token, "true"),
            (self.tenant_b, self.tenant_b_token, "directory-b"),
        ):
            url = reverse("api:scim:user-list", kwargs={"tenant_slug": tenant.slug})
            response = self.client.get(
                f"{url}?filter=externalId eq {external_id!r}",
                **self.headers(token),
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            data = response.json()
            self.assertEqual(data["totalResults"], 1)
            self.assertEqual(data["Resources"][0]["id"], str(user.scim_id))
            self.assertEqual(data["Resources"][0]["externalId"], external_id)

        cross_tenant = self.client.get(
            f'{reverse("api:scim:user-list", kwargs={"tenant_slug": self.tenant_a.slug})}?filter=externalId eq "directory-b"',
            **self.headers(self.tenant_token),
        )
        self.assertEqual(cross_tenant.status_code, status.HTTP_200_OK)
        self.assertEqual(cross_tenant.json()["totalResults"], 0)

        case_variant = self.client.get(
            f'{reverse("api:scim:user-list", kwargs={"tenant_slug": self.tenant_a.slug})}?filter=externalId co "TRUE"',
            **self.headers(self.tenant_token),
        )
        self.assertEqual(case_variant.status_code, status.HTTP_200_OK)
        self.assertEqual(case_variant.json()["totalResults"], 0)

    def test_active_filter_is_scoped_to_membership(self):
        user = User.objects.create_user(username="membership-active-user")
        membership_a = grant(user, self.tenant_a, self.tenant_role).membership
        membership_a.is_active = False
        membership_a.save(update_fields=["is_active"])
        grant(user, self.tenant_b, self.tenant_b_role)

        tenant_a_url = reverse("api:scim:user-list", kwargs={"tenant_slug": self.tenant_a.slug})
        tenant_b_url = reverse("api:scim:user-list", kwargs={"tenant_slug": self.tenant_b.slug})
        response_a = self.client.get(f"{tenant_a_url}?filter=active eq true", **self.headers(self.tenant_token))
        response_b = self.client.get(f"{tenant_b_url}?filter=active eq true", **self.headers(self.tenant_b_token))
        self.assertEqual(response_a.status_code, status.HTTP_200_OK)
        self.assertEqual(response_b.status_code, status.HTTP_200_OK)
        self.assertEqual(response_a.json()["totalResults"], 0)
        self.assertEqual(response_b.json()["totalResults"], 1)

    def test_superuser_token_scope_and_deleted_tenant_are_enforced(self):
        superuser = User.objects.create_superuser(
            username="scim-superuser",
            email="scim-superuser@example.com",
            password="test-password",
        )
        token = Token.objects.create(
            user=superuser, tenant=self.tenant_a, expires=timezone.now() + timezone.timedelta(days=1)
        )
        tenant_b_url = reverse("api:scim:user-list", kwargs={"tenant_slug": self.tenant_b.slug})
        cross_tenant = self.client.get(tenant_b_url, **self.headers(token))
        self.assertEqual(cross_tenant.status_code, status.HTTP_401_UNAUTHORIZED)

        self.tenant_a.deleted_at = timezone.now()
        self.tenant_a.save(update_fields=["deleted_at"])
        deleted_tenant_url = reverse("api:scim:user-list", kwargs={"tenant_slug": self.tenant_a.slug})
        deleted_response = self.client.get(deleted_tenant_url, **self.headers(self.tenant_token))
        self.assertEqual(deleted_response.status_code, status.HTTP_401_UNAUTHORIZED)

        provider_b = Tenant.objects.create(name="Provider B", slug="identity-provider-b", is_provider=True)
        provider_token = Token.objects.create(
            user=superuser,
            tenant=self.provider,
            expires=timezone.now() + timezone.timedelta(days=1),
        )
        provider_b_url = reverse(
            "api:provider_scim:service-provider-config",
            kwargs={"provider_slug": provider_b.slug},
        )
        provider_response = self.client.get(provider_b_url, **self.headers(provider_token))
        self.assertEqual(provider_response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_repeated_tenant_post_with_scoped_external_id_is_idempotent(self):
        url = reverse("api:scim:user-list", kwargs={"tenant_slug": self.tenant_a.slug})
        payload = {
            "userName": "idempotent-user",
            "externalId": "idp-42",
            "emails": [{"value": "idempotent@example.com", "primary": True}],
            "active": True,
        }

        first = self.client.post(url, data=payload, content_type="application/json", **self.headers(self.tenant_token))
        second = self.client.post(url, data=payload, content_type="application/json", **self.headers(self.tenant_token))

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.json()["id"], second.json()["id"])
        user = User.objects.get(username="idempotent-user")
        self.assertEqual(Membership.objects.filter(user=user, tenant=self.tenant_a).count(), 1)
        self.assertEqual(
            Membership.objects.get(user=user, tenant=self.tenant_a).external_id,
            "idp-42",
        )

    def test_cross_tenant_opaque_and_legacy_detail_reads_fail_closed(self):
        foreign_user = User.objects.create_user(username="foreign-user")
        grant(foreign_user, self.tenant_a, self.tenant_role)
        url_base = reverse("api:scim:user-detail", kwargs={"tenant_slug": self.tenant_b.slug, "pk": "placeholder"})
        for identifier in (str(foreign_user.pk), str(foreign_user.scim_id), "not-a-valid-identifier"):
            url = url_base.replace("placeholder", identifier)
            response = self.client.get(url, **self.headers(self.tenant_b_token))
            self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_provider_group_post_accepts_opaque_member_id_and_external_id(self):
        provider_user = User.objects.create_user(username="provider-staff")
        grant(provider_user, self.provider, self.provider_role)
        url = reverse("api:provider_scim:group-list", kwargs={"provider_slug": self.provider.slug})
        payload = {
            "displayName": "Opaque provider group",
            "externalId": "provider-group-1",
            "members": [{"value": str(provider_user.scim_id)}],
        }

        response = self.client.post(
            url, data=payload, content_type="application/json", **self.headers(self.provider_token)
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        group = UserGroup.objects.get(tenant=self.provider, name="Opaque provider group")
        self.assertEqual(group.external_id, "provider-group-1")
        self.assertEqual(data["id"], str(group.scim_id))
        self.assertEqual(data["members"][0]["value"], str(provider_user.scim_id))
        self.assertEqual(GroupMembership.objects.get(user_group=group).external_id, str(provider_user.scim_id))

        repeated = self.client.post(
            url, data=payload, content_type="application/json", **self.headers(self.provider_token)
        )
        self.assertEqual(repeated.status_code, status.HTTP_200_OK)
        self.assertEqual(repeated.json()["id"], str(group.scim_id))
        self.assertEqual(UserGroup.objects.filter(tenant=self.provider, external_id="provider-group-1").count(), 1)

    def test_provider_user_external_id_correlates_and_filters(self):
        url = reverse("api:provider_scim:user-list", kwargs={"provider_slug": self.provider.slug})
        payload = {
            "userName": "provider-correlated",
            "externalId": "provider-user-42",
            "emails": [{"value": "provider-correlated@example.com", "primary": True}],
        }

        first = self.client.post(
            url, data=payload, content_type="application/json", **self.headers(self.provider_token)
        )
        second = self.client.post(
            url, data=payload, content_type="application/json", **self.headers(self.provider_token)
        )

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.json()["id"], second.json()["id"])
        user = User.objects.get(scim_id=first.json()["id"])
        self.assertEqual(Membership.objects.filter(user=user, tenant=self.provider).count(), 1)
        self.assertEqual(
            Membership.objects.get(user=user, tenant=self.provider).external_id,
            "provider-user-42",
        )

        filtered = self.client.get(
            f'{url}?filter=externalId eq "provider-user-42"',
            **self.headers(self.provider_token),
        )
        self.assertEqual(filtered.status_code, status.HTTP_200_OK)
        self.assertEqual(filtered.json()["totalResults"], 1)
        self.assertEqual(filtered.json()["Resources"][0]["id"], str(user.scim_id))

        detail_url = reverse(
            "api:provider_scim:user-detail",
            kwargs={"provider_slug": self.provider.slug, "pk": str(user.scim_id)},
        )
        before = self.client.get(detail_url, **self.headers(self.provider_token))
        patch_response = self.client.patch(
            detail_url,
            data={"Operations": [{"op": "replace", "path": "externalId", "value": "provider-user-43"}]},
            content_type="application/json",
            **self.headers(self.provider_token),
        )
        after = self.client.get(detail_url, **self.headers(self.provider_token))
        self.assertEqual(before.status_code, status.HTTP_200_OK)
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK)
        self.assertEqual(after.status_code, status.HTTP_200_OK)
        self.assertNotEqual(before.json()["meta"]["lastModified"], after.json()["meta"]["lastModified"])

    def test_provider_group_metadata_patch_preserves_inactive_membership(self):
        user = User.objects.create_user(username="inactive-group-user")
        membership = grant(user, self.provider, self.provider_role).membership
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        group = UserGroup.objects.create(tenant=self.provider, name="Inactive group", external_id="before")
        GroupMembership.objects.create(
            user_group=group,
            membership=membership,
            source=GroupMembership.SOURCE_SCIM,
            external_id=str(user.scim_id),
        )
        detail_url = reverse(
            "api:provider_scim:group-detail",
            kwargs={"provider_slug": self.provider.slug, "pk": str(group.scim_id)},
        )
        response = self.client.patch(
            detail_url,
            data={"Operations": [{"op": "replace", "path": "externalId", "value": "after"}]},
            content_type="application/json",
            **self.headers(self.provider_token),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(GroupMembership.objects.filter(user_group=group, membership=membership).exists())

    def test_provider_group_external_id_put_patch_and_filter(self):
        group = UserGroup.objects.create(tenant=self.provider, name="External group")
        detail_url = reverse(
            "api:provider_scim:group-detail",
            kwargs={"provider_slug": self.provider.slug, "pk": str(group.scim_id)},
        )
        put_response = self.client.put(
            detail_url,
            data={"displayName": "External group", "externalId": "group-put", "members": []},
            content_type="application/json",
            **self.headers(self.provider_token),
        )
        self.assertEqual(put_response.status_code, status.HTTP_200_OK)
        group.refresh_from_db()
        self.assertEqual(group.external_id, "group-put")

        patch_response = self.client.patch(
            detail_url,
            data={"Operations": [{"op": "replace", "path": "externalId", "value": "group-patch"}]},
            content_type="application/json",
            **self.headers(self.provider_token),
        )
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK)
        group.refresh_from_db()
        self.assertEqual(group.external_id, "group-patch")
        group_list_url = reverse("api:provider_scim:group-list", kwargs={"provider_slug": self.provider.slug})
        filtered = self.client.get(
            f'{group_list_url}?filter=externalId eq "group-patch"',
            **self.headers(self.provider_token),
        )
        self.assertEqual(filtered.status_code, status.HTTP_200_OK)
        self.assertEqual(filtered.json()["Resources"][0]["id"], str(group.scim_id))

    def test_tenant_mutations_reject_malformed_json_shapes(self):
        headers = self.headers(self.tenant_token)
        user_url = reverse("api:scim:user-list", kwargs={"tenant_slug": self.tenant_a.slug})
        response = self.client.post(user_url, data=["not-an-object"], format="json", **headers)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        user = User.objects.create_user(username="malformed-shape-user")
        grant(user, self.tenant_a, self.tenant_role)
        detail_url = reverse(
            "api:scim:user-detail",
            kwargs={"tenant_slug": self.tenant_a.slug, "pk": user.scim_id},
        )
        response = self.client.put(
            detail_url,
            data={"userName": user.username, "emails": [1]},
            format="json",
            **headers,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response = self.client.patch(
            detail_url,
            data={"Operations": ["not-an-operation"]},
            format="json",
            **headers,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unknown_scim_filter_attribute_is_rejected(self):
        url = reverse("api:scim:user-list", kwargs={"tenant_slug": self.tenant_a.slug})
        response = self.client.get(
            f'{url}?filter=is_superuser eq "true"',
            **self.headers(self.tenant_token),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_empty_and_not_equal_external_id_filters_are_valid(self):
        url = reverse("api:scim:user-list", kwargs={"tenant_slug": self.tenant_a.slug})
        empty_response = self.client.get(
            f'{url}?filter=externalId eq ""',
            **self.headers(self.tenant_token),
        )
        self.assertEqual(empty_response.status_code, status.HTTP_200_OK)
        not_equal_response = self.client.get(
            f'{url}?filter=externalId ne "directory-b"',
            **self.headers(self.tenant_token),
        )
        self.assertEqual(not_equal_response.status_code, status.HTTP_200_OK)

    def test_oversized_legacy_identifiers_fail_closed(self):
        oversized = "9" * 5000
        for value in (oversized, "9223372036854775808"):
            with self.assertRaises(Http404):
                identifier_lookup(value)

        url = reverse("api:scim:user-list", kwargs={"tenant_slug": self.tenant_a.slug})
        response = self.client.get(
            f"{url}?filter=id eq {oversized!r}",
            **self.headers(self.tenant_token),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_malformed_opaque_list_filter_returns_scim_400(self):
        url = reverse("api:scim:user-list", kwargs={"tenant_slug": self.tenant_a.slug})
        response = self.client.get(
            f'{url}?filter=id eq "not-a-uuid"',
            **self.headers(self.tenant_token),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["schemas"], ["urn:ietf:params:scim:api:messages:2.0:Error"])
