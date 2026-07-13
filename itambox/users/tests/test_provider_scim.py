from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant
from users.models import GroupMembership, Token, UserGroup
from core.tests.mixins import grant

User = get_user_model()


class ProviderSCIMProvisioningTests(TestCase):
    """Provider-level SCIM: an MSP is a plain ``Tenant(is_provider=True)`` — there is no
    ``Provider`` model anymore. Authorization is the same standard permission the tenant
    SCIM path uses (``organization.change_membership``, checked via ``user.has_perm``
    against role *content*, never ``Role.name``) held on the provider tenant itself; a
    token is "provider-scoped" simply by its ``tenant`` FK pointing at that tenant.
    Provisioning creates a bare ``Membership`` at the provider tenant with NO
    ``RoleGrant`` — permissions/reach are granted in-app afterwards, never implied
    by SCIM.
    """

    def setUp(self):
        # An ordinary (non-provider) tenant, used both because Token.save() requires a
        # tenant (auto-assigns if unset) and to prove a token scoped to a plain tenant is
        # not a "provider token".
        self.tenant = Tenant.objects.create(name="Acme Corp", slug="acme")

        self.provider = Tenant.objects.create(name="MSP One", slug="msp-one", is_provider=True)
        self.other_provider = Tenant.objects.create(name="MSP Two", slug="msp-two", is_provider=True)

        # Provider-tenant-owned role granting the standard permission that gates this
        # surface. Named deliberately unrelated to "admin"/"staff" to prove authorization
        # is resolved from permission CONTENT, not the role's name (D2-2 regression).
        self.role_staff = Role.objects.create(
            tenant=self.provider,
            name="Tier 2 Grant",
            permissions=[
                'organization.change_membership',
                'users.view_usergroup',
                'users.add_usergroup',
                'users.change_usergroup',
                'users.delete_usergroup',
            ],
        )
        # A role WITHOUT that permission.
        self.role_readonly = Role.objects.create(
            tenant=self.provider,
            name="Read Only",
            permissions=[],
        )

        # Authorised provider-staff user: active Membership at the provider tenant +
        # own-scope RoleGrant carrying the gating permission.
        self.admin_user = User.objects.create_user(
            username="provadmin", email="provadmin@msp.com", password="adminpassword"
        )
        grant(self.admin_user, self.provider, self.role_staff)

        # A user with a membership but lacking the permission.
        self.weak_user = User.objects.create_user(
            username="weak", email="weak@msp.com", password="password123"
        )
        grant(self.weak_user, self.provider, self.role_readonly)

        # Tokens. Token.key plaintext is available right after create().
        self.valid_token = Token.objects.create(
            user=self.admin_user,
            tenant=self.provider,
            expires=timezone.now() + timezone.timedelta(days=1),
        )
        # Token scoped to an ordinary (non-provider) tenant — NOT a provider token, even
        # though its user also has an active membership at the provider tenant.
        self.unscoped_token = Token.objects.create(
            user=self.admin_user,
            tenant=self.tenant,
            expires=timezone.now() + timezone.timedelta(days=1),
        )
        # Token scoped to the provider tenant, but its user lacks the gating permission.
        self.weak_token = Token.objects.create(
            user=self.weak_user,
            tenant=self.provider,
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

    def test_token_not_scoped_to_provider_rejected(self):
        """A token whose ``tenant`` is an ordinary tenant (not this provider) is rejected
        even though its user holds an active, sufficiently-permissioned membership at the
        provider tenant — the token's own scope is checked, not just the user's access."""
        url = reverse('api:provider_scim:user-list', kwargs={'provider_slug': self.provider.slug})
        response = self.client.get(url, HTTP_AUTHORIZATION=f'Bearer {self.unscoped_token.key}')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_token_without_permission_rejected(self):
        url = reverse('api:provider_scim:user-list', kwargs={'provider_slug': self.provider.slug})
        response = self.client.get(url, HTTP_AUTHORIZATION=f'Bearer {self.weak_token.key}')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_group_endpoints_require_method_specific_group_permissions(self):
        role = Role.objects.create(
            tenant=self.provider,
            name='SCIM identity only',
            permissions=['organization.change_membership'],
        )
        user = User.objects.create_user(username='scim-identity-only')
        grant(user, self.provider, role)
        token = Token.objects.create(
            user=user,
            tenant=self.provider,
            expires=timezone.now() + timezone.timedelta(days=1),
        )
        headers = {'HTTP_AUTHORIZATION': f'Bearer {token.key}'}
        group = UserGroup.objects.create(tenant=self.provider, name='Protected group')
        list_url = reverse(
            'api:provider_scim:group-list',
            kwargs={'provider_slug': self.provider.slug},
        )
        detail_url = reverse(
            'api:provider_scim:group-detail',
            kwargs={'provider_slug': self.provider.slug, 'pk': group.pk},
        )
        cases = [
            ('get', list_url, None),
            ('post', list_url, {'displayName': 'Denied create'}),
            ('get', detail_url, None),
            ('put', detail_url, {'displayName': 'Denied update'}),
            ('patch', detail_url, {'Operations': []}),
            ('delete', detail_url, None),
        ]

        for method, url, payload in cases:
            with self.subTest(method=method, url=url):
                client_method = getattr(self.client, method)
                if payload is None:
                    response = client_method(url, **headers)
                else:
                    response = client_method(
                        url,
                        data=payload,
                        content_type='application/json',
                        **headers,
                    )
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_token_scoped_to_other_provider_rejected(self):
        """D2-1 regression (cross-tenant token isolation): valid_token is scoped
        (token.tenant) to self.provider; presenting it against other_provider's mount
        must fail on the token-scope check alone, regardless of the user's permissions
        anywhere else."""
        url = reverse('api:provider_scim:user-list', kwargs={'provider_slug': self.other_provider.slug})
        response = self.client.get(url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authorization_is_permission_content_not_role_name(self):
        """D2-2 regression: authorization must never match on ``Role.name`` — only on
        resolved permission content via ``user.has_perm``. A role literally named
        "Administrator" grants nothing if its permissions list is empty; a role with an
        unrelated name but the actual permission passes.
        """
        decoy_role = Role.objects.create(tenant=self.provider, name="Administrator", permissions=[])
        decoy_user = User.objects.create_user(username="decoy", email="decoy@msp.com")
        grant(decoy_user, self.provider, decoy_role)
        decoy_token = Token.objects.create(
            user=decoy_user, tenant=self.provider,
            expires=timezone.now() + timezone.timedelta(days=1),
        )
        url = reverse('api:provider_scim:user-list', kwargs={'provider_slug': self.provider.slug})
        response = self.client.get(url, HTTP_AUTHORIZATION=f'Bearer {decoy_token.key}')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        legit_role = Role.objects.create(
            tenant=self.provider, name="Zzz Custom Grant 42",
            permissions=['organization.change_membership'],
        )
        legit_user = User.objects.create_user(username="legit", email="legit@msp.com")
        grant(legit_user, self.provider, legit_role)
        legit_token = Token.objects.create(
            user=legit_user, tenant=self.provider,
            expires=timezone.now() + timezone.timedelta(days=1),
        )
        response = self.client.get(url, HTTP_AUTHORIZATION=f'Bearer {legit_token.key}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_superuser_token_bypasses_permission_check(self):
        """Superusers pass regardless of role/permission content (but the token must
        still be scoped to this provider tenant)."""
        super_user = User.objects.create_superuser(username="root", email="root@msp.com", password="x")
        super_token = Token.objects.create(
            user=super_user, tenant=self.provider,
            expires=timezone.now() + timezone.timedelta(days=1),
        )
        url = reverse('api:provider_scim:user-list', kwargs={'provider_slug': self.provider.slug})
        response = self.client.get(url, HTTP_AUTHORIZATION=f'Bearer {super_token.key}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    # ---- Users --------------------------------------------------------------------------

    def test_user_list_returns_provider_staff(self):
        url = reverse('api:provider_scim:user-list', kwargs={'provider_slug': self.provider.slug})
        response = self.client.get(url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        usernames = {r["userName"] for r in data["Resources"]}
        # Both staff users (admin + weak) are active members of this provider tenant —
        # list membership is not gated by role/permission.
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
            Membership.objects.filter(user=user, tenant=self.provider, is_active=True).exists()
        )
        # SCIM provisions identity only: no RoleGrant is auto-created — permissions
        # and reach are granted in-app afterwards, never implied by provisioning.
        self.assertFalse(RoleGrant.objects.filter(membership__user=user).exists())

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
            Membership.objects.get(user=user, tenant=self.provider).is_active
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
        Membership.objects.filter(user=user, tenant=self.provider).update(is_active=True)
        response = self.client.delete(detail_url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Membership.objects.filter(user=user, tenant=self.provider).exists())
        # The User row survives.
        self.assertTrue(User.objects.filter(id=pk).exists())

    # ---- Groups -------------------------------------------------------------------------

    def test_group_list_and_create(self):
        list_url = reverse('api:provider_scim:group-list', kwargs={'provider_slug': self.provider.slug})

        # Empty to start.
        response = self.client.get(list_url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["totalResults"], 0)

        # POST creates a provider-tenant-owned group with a provider-staff member.
        payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
            "displayName": "Senior Technicians",
            "members": [{"value": str(self.admin_user.id)}],
        }
        response = self.client.post(list_url, data=payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.json()["displayName"], "Senior Technicians")

        group = UserGroup.objects.get(name="Senior Technicians")
        self.assertEqual(group.tenant, self.provider)
        self.assertTrue(GroupMembership.objects.filter(
            user_group=group,
            membership__user=self.admin_user,
        ).exists())
        group_membership = GroupMembership.objects.get(user_group=group)
        self.assertEqual(group_membership.source, GroupMembership.SOURCE_SCIM)
        self.assertEqual(group_membership.external_id, str(self.admin_user.id))
        self.assertEqual(group_membership.added_by, self.admin_user)

        # Now the list shows it.
        response = self.client.get(list_url, **self.auth_headers)
        self.assertEqual(response.json()["totalResults"], 1)

        # Duplicate name -> 409.
        response = self.client.post(list_url, data=payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_group_member_guard_skips_non_staff(self):
        """A user who is NOT an active member of this provider tenant is silently
        skipped on group sync."""
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
        self.assertTrue(GroupMembership.objects.filter(
            user_group=group,
            membership__user=self.admin_user,
        ).exists())
        self.assertFalse(GroupMembership.objects.filter(
            user_group=group,
            membership__user=outsider,
        ).exists())

    def test_group_sync_rejects_self_and_other_escalation(self):
        dangerous_role = Role.objects.create(
            tenant=self.provider,
            name='Dangerous inherited role',
            permissions=['assets.delete_asset'],
        )
        group = UserGroup.objects.create(
            tenant=self.provider,
            name='Privileged SCIM group',
        )
        role_grant = RoleGrant.objects.create(user_group=group, role=dangerous_role)
        RoleGrantScope.objects.create(
            role_grant=role_grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )
        detail_url = reverse(
            'api:provider_scim:group-detail',
            kwargs={'provider_slug': self.provider.slug, 'pk': group.pk},
        )

        for user in (self.admin_user, self.weak_user):
            with self.subTest(user=user.username):
                payload = {
                    'Operations': [{
                        'op': 'add',
                        'path': 'members',
                        'value': [{'value': str(user.pk)}],
                    }],
                }
                response = self.client.patch(
                    detail_url,
                    data=payload,
                    content_type='application/json',
                    **self.auth_headers,
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn('Privilege escalation', str(response.json()))
                self.assertFalse(GroupMembership.objects.filter(
                    user_group=group,
                    membership__user=user,
                ).exists())

    def test_put_and_patch_reconcile_only_scim_memberships(self):
        group = UserGroup.objects.create(tenant=self.provider, name='Mixed provenance')
        admin_membership = Membership.objects.get(
            tenant=self.provider,
            user=self.admin_user,
        )
        weak_membership = Membership.objects.get(
            tenant=self.provider,
            user=self.weak_user,
        )
        scim_user = User.objects.create_user(username='scim-owned-member')
        scim_membership = Membership.objects.create(
            tenant=self.provider,
            user=scim_user,
        )
        manual_row = GroupMembership.objects.create(
            user_group=group,
            membership=admin_membership,
            source=GroupMembership.SOURCE_MANUAL,
            external_id='manual-record',
            added_by=self.weak_user,
        )
        ldap_row = GroupMembership.objects.create(
            user_group=group,
            membership=weak_membership,
            source=GroupMembership.SOURCE_LDAP,
            external_id='ldap-record',
        )
        scim_row = GroupMembership.objects.create(
            user_group=group,
            membership=scim_membership,
            source=GroupMembership.SOURCE_SCIM,
            external_id=str(scim_user.pk),
            added_by=self.admin_user,
        )
        detail_url = reverse(
            'api:provider_scim:group-detail',
            kwargs={'provider_slug': self.provider.slug, 'pk': group.pk},
        )

        put_payload = {
            'displayName': 'Mixed provenance renamed',
            'members': [{'value': str(self.admin_user.pk)}],
        }
        response = self.client.put(
            detail_url,
            data=put_payload,
            content_type='application/json',
            **self.auth_headers,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(GroupMembership.objects.filter(pk=scim_row.pk).exists())

        for row, expected_source, expected_external_id, expected_actor in (
            (manual_row, GroupMembership.SOURCE_MANUAL, 'manual-record', self.weak_user),
            (ldap_row, GroupMembership.SOURCE_LDAP, 'ldap-record', None),
        ):
            row.refresh_from_db()
            self.assertEqual(row.source, expected_source)
            self.assertEqual(row.external_id, expected_external_id)
            self.assertEqual(row.added_by, expected_actor)

        patch_payload = {
            'Operations': [{'op': 'replace', 'path': 'members', 'value': []}],
        }
        response = self.client.patch(
            detail_url,
            data=patch_payload,
            content_type='application/json',
            **self.auth_headers,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(GroupMembership.objects.filter(pk=manual_row.pk).exists())
        self.assertTrue(GroupMembership.objects.filter(pk=ldap_row.pk).exists())

    def test_group_detail_put_patch_delete(self):
        group = UserGroup.objects.create(tenant=self.provider, name="Editable Group")
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
        self.assertTrue(GroupMembership.objects.filter(
            user_group=group,
            membership__user=self.admin_user,
        ).exists())

        # PATCH removes the member.
        patch_payload = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "remove", "path": f'members[value eq "{self.admin_user.id}"]'}],
        }
        response = self.client.patch(detail_url, data=patch_payload, content_type='application/json', **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        group.refresh_from_db()
        self.assertFalse(GroupMembership.objects.filter(
            user_group=group,
            membership__user=self.admin_user,
        ).exists())
        self.assertFalse(GroupMembership.objects.filter(user_group=group).exists())

        # DELETE soft-deletes.
        response = self.client.delete(detail_url, **self.auth_headers)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(UserGroup.objects.filter(id=group.id).exists())

    def test_provider_group_isolation(self):
        """Groups owned by another provider tenant are not visible/editable through this
        provider's SCIM mount."""
        other_group = UserGroup.objects.create(tenant=self.other_provider, name="Other Provider Group")
        list_url = reverse('api:provider_scim:group-list', kwargs={'provider_slug': self.provider.slug})
        response = self.client.get(list_url, **self.auth_headers)
        self.assertEqual(response.json()["totalResults"], 0)

        detail_url = reverse('api:provider_scim:group-detail', kwargs={'provider_slug': self.provider.slug, 'pk': other_group.id})
        self.assertEqual(self.client.get(detail_url, **self.auth_headers).status_code, status.HTTP_404_NOT_FOUND)
