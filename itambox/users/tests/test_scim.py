import base64
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from organization.models import Tenant, Membership, Role, AssetHolder
from users.models import Token, UserGroup
from rest_framework import status

User = get_user_model()


class SCIMProvisioningTests(TestCase):
    def setUp(self):
        # Create Tenants
        self.tenant = Tenant.objects.create(name="Acme Corp", slug="acme")
        self.other_tenant = Tenant.objects.create(name="Other Corp", slug="other")

        # Create Users
        self.admin_user = User.objects.create_user(
            username="admin", email="admin@acme.com", password="adminpassword"
        )
        self.inactive_user = User.objects.create_user(
            username="inactive", email="inactive@acme.com", password="password123", is_active=False
        )
        self.no_membership_user = User.objects.create_user(
            username="nomember", email="nomember@acme.com", password="password123"
        )

        # Create Tenant Roles
        self.role_member = Role.objects.create(
            tenant=self.tenant,
            name="Member",
            permissions=["assets.view_asset", "extras.view_dashboard"]
        )
        self.role_admin = Role.objects.create(
            tenant=self.tenant,
            name="Admin",
            permissions=["assets.view_asset", "assets.add_asset", "extras.view_dashboard"]
        )

        # Create Tenant Memberships — create first, then add roles (no role= kwarg).
        admin_membership = Membership.objects.create(user=self.admin_user,
            tenant=self.tenant,
        )
        admin_membership.roles.add(self.role_admin)

        # Setup tokens
        self.valid_token = Token.objects.create(
            user=self.admin_user,
            expires=timezone.now() + timezone.timedelta(days=1)
        )
        self.expired_token = Token.objects.create(
            user=self.admin_user,
            expires=timezone.now() - timezone.timedelta(hours=1)
        )
        self.inactive_token = Token.objects.create(
            user=self.inactive_user,
            expires=timezone.now() + timezone.timedelta(days=1)
        )
        self.no_membership_token = Token.objects.create(
            user=self.no_membership_user,
            expires=timezone.now() + timezone.timedelta(days=1)
        )

        # Headers helpers
        self.auth_headers = {
            'HTTP_AUTHORIZATION': f'Bearer {self.valid_token.key}'
        }

    def test_authentication_scenarios(self):
        url = reverse('api:scim:service-provider-config', kwargs={'tenant_slug': self.tenant.slug})

        # 1. No authentication
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # 2. Valid token authentication
        response = self.client.get(url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # 3. Invalid token key
        response = self.client.get(url, HTTP_AUTHORIZATION='Bearer invalidkey')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # 4. Expired token key
        response = self.client.get(url, HTTP_AUTHORIZATION=f'Bearer {self.expired_token.key}')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # 5. Inactive user token
        response = self.client.get(url, HTTP_AUTHORIZATION=f'Bearer {self.inactive_token.key}')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # 6. User without tenant membership
        response = self.client.get(url, HTTP_AUTHORIZATION=f'Bearer {self.no_membership_token.key}')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # 7. HTTP Basic auth is no longer accepted on SCIM endpoints (removed to
        #    avoid transmitting credentials on every request) — rejected even
        #    with otherwise-valid credentials. Bearer token is now required.
        basic_credentials = base64.b64encode(b'admin:adminpassword').decode('utf-8')
        response = self.client.get(url, HTTP_AUTHORIZATION=f'Basic {basic_credentials}')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        # 8. Basic Authentication fallback failure
        basic_credentials_fail = base64.b64encode(b'admin:wrongpassword').decode('utf-8')
        response = self.client.get(url, HTTP_AUTHORIZATION=f'Basic {basic_credentials_fail}')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_service_provider_config(self):
        url = reverse('api:scim:service-provider-config', kwargs={'tenant_slug': self.tenant.slug})
        response = self.client.get(url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn("urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig", data["schemas"])
        self.assertTrue(data["patch"]["supported"])

    def test_user_list_and_filtering(self):
        url = reverse('api:scim:user-list', kwargs={'tenant_slug': self.tenant.slug})

        # List should include admin_user
        response = self.client.get(url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["totalResults"], 1)
        self.assertEqual(data["Resources"][0]["userName"], self.admin_user.username)

        # Create another user in tenant to test filters — create membership, then add role.
        user2 = User.objects.create_user(username="user2", email="user2@acme.com")
        m2 = Membership.objects.create(user=user2, tenant=self.tenant)
        m2.roles.add(self.role_member)

        # Total count is 2
        response = self.client.get(url, **self.auth_headers)
        self.assertEqual(response.json()["totalResults"], 2)

        # Test eq filter
        response = self.client.get(f"{url}?filter=userName eq \"user2\"", **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["totalResults"], 1)
        self.assertEqual(data["Resources"][0]["userName"], "user2")

        # Test filters co
        response = self.client.get(f"{url}?filter=userName co \"user\"", **self.auth_headers)
        self.assertEqual(response.json()["totalResults"], 1)

    def test_user_creation_and_assetholder_linking(self):
        url = reverse('api:scim:user-list', kwargs={'tenant_slug': self.tenant.slug})
        payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "newuser@example.com",
            "name": {
                "familyName": "Doe",
                "givenName": "John"
            },
            "emails": [
                {
                    "value": "newuser@example.com",
                    "primary": True
                }
            ],
            "active": True
        }

        # Success path
        response = self.client.post(url, data=payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        res_data = response.json()
        self.assertEqual(res_data["userName"], "newuser@example.com")
        self.assertEqual(res_data["name"]["givenName"], "John")

        # Verify User and AssetHolder and Membership.
        # SCIM /Users provisioning creates the membership with NO role assigned —
        # roles are granted in-app via UserGroup, not at provisioning time.
        user = User.objects.get(username="newuser@example.com")
        self.assertTrue(user.is_active)

        membership = Membership.objects.get(user=user, tenant=self.tenant)
        self.assertFalse(membership.roles.exists())

        holder = AssetHolder.objects.get(user=user, tenant=self.tenant)
        self.assertEqual(holder.email, "newuser@example.com")
        self.assertEqual(holder.first_name, "John")
        self.assertEqual(holder.last_name, "Doe")

        # Conflict check in same tenant
        response = self.client.post(url, data=payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_user_creation_linking_existing_assetholder(self):
        # Create unlinked AssetHolder
        unlinked_holder = AssetHolder.objects.create(
            first_name="Jane",
            last_name="Smith",
            upn="jane@acme.com",
            email="jane@acme.com",
            tenant=self.tenant
        )

        url = reverse('api:scim:user-list', kwargs={'tenant_slug': self.tenant.slug})
        payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "jane@acme.com",
            "name": {
                "familyName": "Smith",
                "givenName": "Jane"
            },
            "emails": [
                {
                    "value": "jane@acme.com",
                    "primary": True
                }
            ],
            "active": True
        }

        response = self.client.post(url, data=payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        user = User.objects.get(username="jane@acme.com")
        unlinked_holder.refresh_from_db()
        self.assertEqual(unlinked_holder.user, user)

    def test_user_detail_put_patch_delete(self):
        # Create user — create membership, then add role.
        user = User.objects.create_user(username="testuser", email="test@acme.com")
        m = Membership.objects.create(user=user, tenant=self.tenant)
        m.roles.add(self.role_member)
        AssetHolder.objects.create(
            user=user, first_name="Test", last_name="User", upn="test@acme.com", email="test@acme.com", tenant=self.tenant
        )

        detail_url = reverse('api:scim:user-detail', kwargs={'tenant_slug': self.tenant.slug, 'pk': user.id})

        # 1. GET Details
        response = self.client.get(detail_url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["userName"], "testuser")

        # 2. PUT Update
        put_payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "testuser_updated",
            "name": {
                "familyName": "UpdatedLastName",
                "givenName": "UpdatedFirstName"
            },
            "emails": [{"value": "updated@acme.com", "primary": True}],
            "active": False
        }
        response = self.client.put(detail_url, data=put_payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        user.refresh_from_db()
        self.assertEqual(user.username, "testuser_updated")
        self.assertEqual(user.email, "updated@acme.com")
        self.assertFalse(user.is_active)

        holder = AssetHolder.objects.get(user=user, tenant=self.tenant)
        self.assertEqual(holder.first_name, "UpdatedFirstName")
        self.assertEqual(holder.last_name, "UpdatedLastName")

        # 3. PATCH Update
        patch_payload = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [
                {
                    "op": "replace",
                    "path": "active",
                    "value": True
                }
            ]
        }
        response = self.client.patch(detail_url, data=patch_payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertTrue(user.is_active)

        # 4. DELETE details
        response = self.client.delete(detail_url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertTrue(User.objects.filter(id=user.id).exists())
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertFalse(Membership.objects.filter(user=user, tenant=self.tenant).exists())

    def test_group_endpoint_is_read_only(self):
        """SCIM /Groups is READ-ONLY: user groups are global and managed centrally
        (not provisioned per-tenant via SCIM, which would let one tenant's IdP mint
        cross-tenant groups). Reads are scoped to groups that grant a role in THIS
        tenant; all writes return 403."""
        list_url = reverse('api:scim:group-list', kwargs={'tenant_slug': self.tenant.slug})

        # 1. GET list — no group grants this tenant yet.
        response = self.client.get(list_url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["totalResults"], 0)

        # 2. POST is rejected — groups cannot be created via tenant SCIM.
        post_payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
            "displayName": "Software Managers",
        }
        response = self.client.post(list_url, data=post_payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(UserGroup.objects.filter(name="Software Managers").exists())

        # 3. A global group carrying a role in THIS tenant is visible (read-only).
        group = UserGroup.objects.create(name="Software Managers")
        group.roles.add(self.role_member)  # role_member belongs to self.tenant
        response = self.client.get(list_url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["totalResults"], 1)
        self.assertEqual(response.json()["Resources"][0]["displayName"], "Software Managers")

        # 4. GET detail works.
        detail_url = reverse('api:scim:group-detail', kwargs={'tenant_slug': self.tenant.slug, 'pk': group.id})
        response = self.client.get(detail_url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["displayName"], "Software Managers")

        # 5. PUT / PATCH / DELETE are all rejected; the group is unchanged.
        put_payload = dict(post_payload, displayName="Renamed")
        self.assertEqual(
            self.client.put(detail_url, data=put_payload, content_type='application/json', **self.auth_headers).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        patch_payload = {"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"], "Operations": []}
        self.assertEqual(
            self.client.patch(detail_url, data=patch_payload, content_type='application/json', **self.auth_headers).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.delete(detail_url, **self.auth_headers).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        group.refresh_from_db()
        self.assertEqual(group.name, "Software Managers")
        self.assertTrue(UserGroup.objects.filter(id=group.id).exists())

    def test_filter_parsing_bracketed_emails(self):
        url = reverse('api:scim:user-list', kwargs={'tenant_slug': self.tenant.slug})
        user2 = User.objects.create_user(username="user2", email="user2@acme.com")
        m2 = Membership.objects.create(user=user2, tenant=self.tenant)
        m2.roles.add(self.role_member)

        response = self.client.get(f"{url}?filter=emails[type eq \"work\"].value eq \"user2@acme.com\"", **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["totalResults"], 1)
        self.assertEqual(data["Resources"][0]["userName"], "user2")

    def test_auth_no_tenant_resolved(self):
        response = self.client.get('/api/tenants//scim/v2/Users', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_graceful_assetholder_creation_on_integrity_error(self):
        from unittest.mock import patch
        from django.db import IntegrityError

        url = reverse('api:scim:user-list', kwargs={'tenant_slug': self.tenant.slug})
        payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "mockeduser@example.com",
            "name": {
                "familyName": "Mock",
                "givenName": "User"
            },
            "emails": [
                {
                    "value": "mockeduser@example.com",
                    "primary": True
                }
            ],
            "active": True
        }

        with patch('organization.models.AssetHolder.objects.create', side_effect=IntegrityError("Mocked constraint violation")):
            response = self.client.post(url, data=payload, content_type='application/json', **self.auth_headers)
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.assertTrue(User.objects.filter(username="mockeduser@example.com").exists())

    def test_scim_patch_does_not_mutate_shared_global_user(self):
        """WS1-3: a tenant-A SCIM token must NOT globally deactivate or rename a user who is
        also a member of tenant B (cross-tenant write on a shared principal)."""
        shared = User.objects.create_user(username="shared", email="shared@x.com", is_active=True)
        Membership.objects.create(user=shared, tenant=self.tenant)
        other_role = Role.objects.create(tenant=self.other_tenant, name="Member", permissions=[])
        other_m = Membership.objects.create(user=shared, tenant=self.other_tenant)
        other_m.roles.add(other_role)

        detail_url = reverse('api:scim:user-detail', kwargs={'tenant_slug': self.tenant.slug, 'pk': shared.id})
        patch_payload = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [
                {"op": "replace", "path": "active", "value": False},
                {"op": "replace", "path": "userName", "value": "hijacked"},
            ],
        }
        response = self.client.patch(detail_url, data=patch_payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        shared.refresh_from_db()
        # Global identity/active are untouched — the user stays active & authenticatable to B.
        self.assertTrue(shared.is_active)
        self.assertEqual(shared.username, "shared")
        self.assertTrue(Membership.objects.filter(user=shared, tenant=self.other_tenant).exists())
        # active=false is now applied PER-TENANT: this tenant's membership is suspended,
        # the other tenant's stays active (no cross-tenant write, but a real local revoke).
        self.assertFalse(Membership.objects.get(user=shared, tenant=self.tenant).is_active)
        self.assertTrue(Membership.objects.get(user=shared, tenant=self.other_tenant).is_active)

    def test_scim_active_false_deprovisions_this_tenant_only(self):
        """active=false on a multi-tenant user suspends THIS tenant's membership (revoking
        access here) while leaving the global account and other tenants untouched; the SCIM
        response reflects the per-tenant state, and active=true restores access."""
        from core.auth import TenantMembershipBackend
        backend = TenantMembershipBackend()

        shared = User.objects.create_user(username="shared2", email="shared2@x.com", is_active=True)
        m_this = Membership.objects.create(user=shared, tenant=self.tenant)
        m_this.roles.add(self.role_member)
        other_role = Role.objects.create(
            tenant=self.other_tenant, name="Member", permissions=["assets.view_asset"]
        )
        m_other = Membership.objects.create(user=shared, tenant=self.other_tenant)
        m_other.roles.add(other_role)

        # Baseline: the membership grants access in this tenant.
        self.assertTrue(backend.has_perm(User.objects.get(pk=shared.pk), 'assets.view_asset', obj=self.tenant))

        detail_url = reverse('api:scim:user-detail', kwargs={'tenant_slug': self.tenant.slug, 'pk': shared.id})

        # Deprovision this tenant.
        resp = self.client.patch(detail_url, data={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": False}],
        }, content_type='application/json', **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # SCIM response reports active=false FOR THIS TENANT (not the global flag).
        self.assertFalse(resp.json()['active'])

        shared.refresh_from_db()
        self.assertTrue(shared.is_active)  # global account stays enabled (other tenant active)
        self.assertFalse(Membership.objects.get(user=shared, tenant=self.tenant).is_active)
        self.assertTrue(Membership.objects.get(user=shared, tenant=self.other_tenant).is_active)
        # Access is revoked HERE but unaffected in the other tenant.
        self.assertFalse(backend.has_perm(User.objects.get(pk=shared.pk), 'assets.view_asset', obj=self.tenant))
        self.assertTrue(backend.has_perm(User.objects.get(pk=shared.pk), 'assets.view_asset', obj=self.other_tenant))

        # Reactivate this tenant.
        resp = self.client.patch(detail_url, data={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": True}],
        }, content_type='application/json', **self.auth_headers)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.json()['active'])
        self.assertTrue(Membership.objects.get(user=shared, tenant=self.tenant).is_active)
        self.assertTrue(backend.has_perm(User.objects.get(pk=shared.pk), 'assets.view_asset', obj=self.tenant))

    def test_scim_put_updates_sole_tenant_user(self):
        """Control for WS1-3: a user whose ONLY membership is this tenant is still fully
        updatable (the guard must not over-block single-tenant users)."""
        solo = User.objects.create_user(username="solo", email="solo@acme.com", is_active=True)
        m = Membership.objects.create(user=solo, tenant=self.tenant)
        m.roles.add(self.role_member)
        detail_url = reverse('api:scim:user-detail', kwargs={'tenant_slug': self.tenant.slug, 'pk': solo.id})
        put_payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "solo_renamed",
            "name": {"familyName": "S", "givenName": "Solo"},
            "emails": [{"value": "solo2@acme.com", "primary": True}],
            "active": False,
        }
        response = self.client.put(detail_url, data=put_payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        solo.refresh_from_db()
        self.assertEqual(solo.username, "solo_renamed")
        self.assertFalse(solo.is_active)

    def test_scim_group_post_is_rejected(self):
        """Group creation via tenant SCIM is rejected outright (groups are global and
        managed centrally), so it cannot create a group, provision a foreign user, or
        leak usernames."""
        foreign_role = Role.objects.create(tenant=self.other_tenant, name="Member", permissions=[])
        foreign_user = User.objects.create_user(username="foreignuser", email="foreign@other.com")
        fm = Membership.objects.create(user=foreign_user, tenant=self.other_tenant)
        fm.roles.add(foreign_role)

        list_url = reverse('api:scim:group-list', kwargs={'tenant_slug': self.tenant.slug})
        payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
            "displayName": "Injected Group",
            "members": [{"value": str(foreign_user.id)}],
        }
        response = self.client.post(list_url, data=payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Nothing was created or provisioned.
        self.assertFalse(UserGroup.objects.filter(name="Injected Group").exists())
        self.assertFalse(Membership.objects.filter(user=foreign_user, tenant=self.tenant).exists())
        self.assertFalse(AssetHolder.objects.filter(user=foreign_user, tenant=self.tenant).exists())
