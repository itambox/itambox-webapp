from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from organization.models import Tenant, Provider, Role
from users.models import Token, UserGroup
from organization.models import Membership

User = get_user_model()


class ProviderSCIMProvisioningTests(TestCase):
    def setUp(self):
        # A tenant is still needed because Token.save() requires one (auto-assigns if unset).
        self.tenant = Tenant.objects.create(name="Acme Corp", slug="acme")

        self.provider = Provider.objects.create(name="MSP One", slug="msp-one")
        self.other_provider = Provider.objects.create(name="MSP Two", slug="msp-two")

        # Provider-scoped role granting the manage_staff capability.
        self.role_staff = Role.objects.create(
            provider=self.provider,
            name="Staff Admin",
            permissions=['organization.manage_staff'],
        )
        # A role WITHOUT the capability.
        self.role_readonly = Role.objects.create(
            provider=self.provider,
            name="Read Only",
            permissions=[],
        )

        # Authorised provider-staff user + active Membership.
        self.admin_user = User.objects.create_user(
            username="provadmin", email="provadmin@msp.com", password="adminpassword"
        )
        m_admin = Membership.objects.create(
            person_type=Membership.PERSON_STAFF, user=self.admin_user, provider=self.provider,
            is_active=True,
        )
        m_admin.roles.add(self.role_staff)

        # A user with a membership but no capability.
        self.weak_user = User.objects.create_user(
            username="weak", email="weak@msp.com", password="password123"
        )
        m_weak = Membership.objects.create(
            person_type=Membership.PERSON_STAFF, user=self.weak_user, provider=self.provider,
            is_active=True,
        )
        m_weak.roles.add(self.role_readonly)

        # Tokens. Token.key plaintext is available right after create().
        self.valid_token = Token.objects.create(
            user=self.admin_user,
            provider=self.provider,
            tenant=self.tenant,
            expires=timezone.now() + timezone.timedelta(days=1),
        )
        # Token scoped to the user but NOT to any provider (no provider scope).
        self.unscoped_token = Token.objects.create(
            user=self.admin_user,
            tenant=self.tenant,
            expires=timezone.now() + timezone.timedelta(days=1),
        )
        # Token whose user lacks the capability.
        self.weak_token = Token.objects.create(
            user=self.weak_user,
            provider=self.provider,
            tenant=self.tenant,
            expires=timezone.now() + timezone.timedelta(days=1),
        )

        self.auth_headers = {'HTTP_AUTHORIZATION': f'Bearer {self.valid_token.key}'}

    # ---- Authentication / authorization -------------------------------------------------

    def test_no_auth_is_unauthorized(self):
        url = reverse('api:provider_scim:service-provider-config', kwargs={'provider_slug': self.provider.slug})
        self.assertEqual(self.client.get(url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_valid_token_passes(self):
        url = reverse('api:provider_scim:service-provider-config', kwargs={'provider_slug': self.provider.slug})
        response = self.client.get(url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(
            "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig",
            response.json()["schemas"],
        )

    def test_token_without_provider_scope_rejected(self):
        url = reverse('api:provider_scim:user-list', kwargs={'provider_slug': self.provider.slug})
        response = self.client.get(url, HTTP_AUTHORIZATION=f'Bearer {self.unscoped_token.key}')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_token_without_capability_rejected(self):
        url = reverse('api:provider_scim:user-list', kwargs={'provider_slug': self.provider.slug})
        response = self.client.get(url, HTTP_AUTHORIZATION=f'Bearer {self.weak_token.key}')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_token_scoped_to_other_provider_rejected(self):
        # valid_token is scoped to self.provider; using it against other_provider must fail
        # both the scope check and the capability check.
        url = reverse('api:provider_scim:user-list', kwargs={'provider_slug': self.other_provider.slug})
        response = self.client.get(url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # ---- Users --------------------------------------------------------------------------

    def test_user_list_returns_provider_staff(self):
        url = reverse('api:provider_scim:user-list', kwargs={'provider_slug': self.provider.slug})
        response = self.client.get(url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        usernames = {r["userName"] for r in data["Resources"]}
        # Both staff users (admin + weak) are active members of this provider.
        self.assertEqual(data["totalResults"], 2)
        self.assertIn("provadmin", usernames)
        self.assertIn("weak", usernames)

    def test_user_post_creates_user_and_membership(self):
        url = reverse('api:provider_scim:user-list', kwargs={'provider_slug': self.provider.slug})
        payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "newstaff@msp.com",
            "name": {"familyName": "Doe", "givenName": "John"},
            "emails": [{"value": "newstaff@msp.com", "primary": True}],
            "active": True,
        }
        response = self.client.post(url, data=payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.json()["userName"], "newstaff@msp.com")

        user = User.objects.get(username="newstaff@msp.com")
        self.assertTrue(user.is_active)
        self.assertTrue(
            Membership.objects.filter(user=user, provider=self.provider, is_active=True).exists()
        )

        # Conflict on duplicate membership.
        response = self.client.post(url, data=payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_user_detail_get_put_patch_delete(self):
        url = reverse('api:provider_scim:user-list', kwargs={'provider_slug': self.provider.slug})
        payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "lifecycle@msp.com",
            "name": {"familyName": "Cycle", "givenName": "Life"},
            "emails": [{"value": "lifecycle@msp.com", "primary": True}],
            "active": True,
        }
        created = self.client.post(url, data=payload, content_type='application/json', **self.auth_headers).json()
        pk = created["id"]
        detail_url = reverse('api:provider_scim:user-detail', kwargs={'provider_slug': self.provider.slug, 'pk': pk})

        # GET
        self.assertEqual(self.client.get(detail_url, **self.auth_headers).status_code, status.HTTP_200_OK)

        # PUT: this user's only membership is this provider, so global identity is editable.
        put_payload = dict(payload, userName="lifecycle_renamed", active=False)
        response = self.client.put(detail_url, data=put_payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user = User.objects.get(id=pk)
        self.assertEqual(user.username, "lifecycle_renamed")
        self.assertFalse(
            Membership.objects.get(user=user, provider=self.provider).is_active
        )

        # PATCH active back to true. Detail queryset requires active membership, so first
        # reactivate via PATCH against the (still-resolvable-by-pk) staff set: the user is no
        # longer active staff, so it 404s — confirming detail is scoped to active staff.
        patch_payload = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": True}],
        }
        response = self.client.patch(detail_url, data=patch_payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # Reactivate directly, then DELETE removes the membership.
        Membership.objects.filter(user=user, provider=self.provider).update(is_active=True)
        response = self.client.delete(detail_url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Membership.objects.filter(user=user, provider=self.provider).exists())
        # The User row survives.
        self.assertTrue(User.objects.filter(id=pk).exists())

    # ---- Groups -------------------------------------------------------------------------

    def test_group_list_and_create(self):
        list_url = reverse('api:provider_scim:group-list', kwargs={'provider_slug': self.provider.slug})

        # Empty to start.
        response = self.client.get(list_url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["totalResults"], 0)

        # POST creates a provider-scoped group with a provider-staff member.
        payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
            "displayName": "Senior Technicians",
            "members": [{"value": str(self.admin_user.id)}],
        }
        response = self.client.post(list_url, data=payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.json()["displayName"], "Senior Technicians")

        group = UserGroup.objects.get(name="Senior Technicians")
        self.assertEqual(group.provider, self.provider)
        self.assertIn(self.admin_user, group.members.all())

        # Now the list shows it.
        response = self.client.get(list_url, **self.auth_headers)
        self.assertEqual(response.json()["totalResults"], 1)

        # Duplicate name -> 409.
        response = self.client.post(list_url, data=payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_group_member_guard_skips_non_staff(self):
        """A user who is NOT active staff of this provider is silently skipped on group sync."""
        outsider = User.objects.create_user(username="outsider", email="out@x.com")
        list_url = reverse('api:provider_scim:group-list', kwargs={'provider_slug': self.provider.slug})
        payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
            "displayName": "Guarded Group",
            "members": [{"value": str(outsider.id)}, {"value": str(self.admin_user.id)}],
        }
        response = self.client.post(list_url, data=payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        group = UserGroup.objects.get(name="Guarded Group")
        self.assertIn(self.admin_user, group.members.all())
        self.assertNotIn(outsider, group.members.all())

    def test_group_detail_put_patch_delete(self):
        group = UserGroup.objects.create(provider=self.provider, name="Editable Group")
        detail_url = reverse('api:provider_scim:group-detail', kwargs={'provider_slug': self.provider.slug, 'pk': group.id})

        # PUT renames + sets members.
        put_payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
            "displayName": "Renamed Group",
            "members": [{"value": str(self.admin_user.id)}],
        }
        response = self.client.put(detail_url, data=put_payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        group.refresh_from_db()
        self.assertEqual(group.name, "Renamed Group")
        self.assertIn(self.admin_user, group.members.all())

        # PATCH removes the member.
        patch_payload = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "remove", "path": f'members[value eq "{self.admin_user.id}"]'}],
        }
        response = self.client.patch(detail_url, data=patch_payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        group.refresh_from_db()
        self.assertNotIn(self.admin_user, group.members.all())

        # DELETE soft-deletes.
        response = self.client.delete(detail_url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(UserGroup.objects.filter(id=group.id).exists())

    def test_provider_group_isolation(self):
        """Groups of another provider are not visible/editable through this provider's SCIM."""
        other_group = UserGroup.objects.create(provider=self.other_provider, name="Other Provider Group")
        list_url = reverse('api:provider_scim:group-list', kwargs={'provider_slug': self.provider.slug})
        response = self.client.get(list_url, **self.auth_headers)
        self.assertEqual(response.json()["totalResults"], 0)

        detail_url = reverse('api:provider_scim:group-detail', kwargs={'provider_slug': self.provider.slug, 'pk': other_group.id})
        self.assertEqual(self.client.get(detail_url, **self.auth_headers).status_code, status.HTTP_404_NOT_FOUND)
