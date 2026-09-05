"""Security and determinism tests for the Organization access-scope authority."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from core.tenant_scope import build_accessible_tenant_permissions_map
from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant, TenantGroup
from organization.services.access_scope import (
    AccessScopeDeniedDTO,
    AccessScopeResolutionRequestDTO,
    AccessScopeResolvedDTO,
    ActorContextDTO,
    RequestedScopeSelectorDTO,
    ResolvedAccessAuthorizationDTO,
    TenantGroupId,
    TenantId,
    authentication_revision_for_actor,
    reauthorize_access_scope,
    resolve_access_scope,
)
from users.models import GroupMembership, UserGroup

User = get_user_model()


class SpecificationAccessScopeTests(TestCase):
    def setUp(self):
        self.group = TenantGroup.objects.create(name="Scope Group", slug="scope-group")
        self.provider = Tenant.objects.create(
            name="Scope Provider",
            slug="scope-provider",
            is_provider=True,
        )
        self.tenant_a = Tenant.objects.create(
            name="Scope Tenant A",
            slug="scope-tenant-a",
            managed_by=self.provider,
            group=self.group,
        )
        self.tenant_b = Tenant.objects.create(
            name="Scope Tenant B",
            slug="scope-tenant-b",
            managed_by=self.provider,
            group=self.group,
        )
        self.user = User.objects.create_user(username="scope-user")
        self.membership = Membership.objects.create(user=self.user, tenant=self.provider)
        self.role = Role.objects.create(
            tenant=self.provider,
            name="Scope reader",
            permissions=["assets.view_asset"],
        )

    def _actor(self, user=None):
        user = user or self.user
        return ActorContextDTO(
            actor_id=user.pk,
            authentication_revision=authentication_revision_for_actor(user),
        )

    def _request(self, *, actor=None, mode="tenant", tenant_id=None, tenant_group_id=None, permission="assets.view_asset"):
        selector = RequestedScopeSelectorDTO(
            mode=mode,
            tenant_id=tenant_id,
            tenant_group_id=tenant_group_id,
        )
        return AccessScopeResolutionRequestDTO(
            actor=actor or self._actor(),
            selector=selector,
            operation="read_asset",
            required_permission=permission,
        )

    def _grant(self, *, scope_type, tenant=None, tenant_group=None, user_group=None, valid_until=None):
        grant = RoleGrant.objects.create(
            membership=self.membership if user_group is None else None,
            user_group=user_group,
            role=self.role,
            valid_until=valid_until,
        )
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=scope_type,
            tenant=tenant,
            tenant_group=tenant_group,
        )
        return grant

    def test_selector_combinations_are_syntax_errors(self):
        cases = (
            {"mode": "tenant", "tenant_id": None, "tenant_group_id": None},
            {"mode": "tenant", "tenant_id": self.tenant_a.pk, "tenant_group_id": self.group.pk},
            {"mode": "tenant_group", "tenant_id": self.tenant_a.pk, "tenant_group_id": self.group.pk},
            {"mode": "tenant_group", "tenant_id": None, "tenant_group_id": None},
            {"mode": "all_accessible", "tenant_id": self.tenant_a.pk, "tenant_group_id": None},
            {"mode": "unknown", "tenant_id": None, "tenant_group_id": None},
        )
        for values in cases:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    RequestedScopeSelectorDTO(**values)

    def test_superuser_and_staff_flags_do_not_bypass_rbac(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save(update_fields=["is_superuser", "is_staff"])

        result = resolve_access_scope(self._request(tenant_id=TenantId(self.tenant_a.pk)))

        self.assertIsInstance(result, AccessScopeDeniedDTO)
        self.assertEqual(result.public_code, "OBJECT_UNAVAILABLE")
        self.assertEqual(result.public_path, ())

    def test_exact_tenant_scope_uses_permission_intersection_and_expiry(self):
        expiry = timezone.now() + timedelta(hours=2)
        self._grant(scope_type=RoleGrantScope.SCOPE_TENANT, tenant=self.tenant_a, valid_until=expiry)

        result = resolve_access_scope(self._request(tenant_id=TenantId(self.tenant_a.pk)))

        self.assertIsInstance(result, AccessScopeResolvedDTO)
        self.assertEqual(result.access_scope.authorized_tenant_ids, frozenset({self.tenant_a.pk}))
        self.assertEqual(result.access_scope.valid_until_epoch_seconds, int(expiry.timestamp()))
        self.assertTrue(result.access_scope.selector_fingerprint)
        self.assertTrue(result.access_scope.authorization_revision)
        self.assertTrue(result.access_scope.access_scope_fingerprint)

    def test_group_scope_requires_complete_authorized_selection(self):
        self._grant(scope_type=RoleGrantScope.SCOPE_TENANT_GROUP, tenant_group=self.group)

        result = resolve_access_scope(
            self._request(
                mode="tenant_group",
                tenant_group_id=TenantGroupId(self.group.pk),
            )
        )

        self.assertIsInstance(result, AccessScopeResolvedDTO)
        self.assertEqual(
            result.access_scope.authorized_tenant_ids,
            frozenset({self.tenant_a.pk, self.tenant_b.pk}),
        )

    def test_group_scope_denies_partial_authorization_without_detail(self):
        self._grant(scope_type=RoleGrantScope.SCOPE_TENANT, tenant=self.tenant_a)

        result = resolve_access_scope(
            self._request(
                mode="tenant_group",
                tenant_group_id=TenantGroupId(self.group.pk),
            )
        )

        self.assertEqual(result, AccessScopeDeniedDTO("denied", "OBJECT_UNAVAILABLE", ()))
        self.assertFalse(hasattr(result, "authorized_tenant_ids"))

    def test_reauthorization_replays_original_request_after_permission_removal(self):
        self._grant(scope_type=RoleGrantScope.SCOPE_TENANT, tenant=self.tenant_a)
        request = self._request(tenant_id=TenantId(self.tenant_a.pk))
        initial = resolve_access_scope(request)
        self.assertIsInstance(initial, AccessScopeResolvedDTO)
        authorization = initial.access_scope

        original = ResolvedAccessAuthorizationDTO(
            actor=request.actor,
            request=request,
            initial_scope=authorization,
        )
        self.role.permissions = ["assets.change_asset"]
        self.role.save(update_fields=["permissions"])

        result = reauthorize_access_scope(original)

        self.assertIsInstance(result, AccessScopeDeniedDTO)
        self.assertEqual(result.public_code, "OBJECT_UNAVAILABLE")

    def test_reauthorization_fails_closed_after_grant_expiry(self):
        grant = self._grant(
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.tenant_a,
            valid_until=timezone.now() + timedelta(hours=1),
        )
        request = self._request(tenant_id=TenantId(self.tenant_a.pk))
        initial = resolve_access_scope(request)
        self.assertIsInstance(initial, AccessScopeResolvedDTO)
        authorization = ResolvedAccessAuthorizationDTO(
            actor=request.actor,
            request=request,
            initial_scope=initial.access_scope,
        )
        grant.valid_until = timezone.now() - timedelta(seconds=1)
        grant.save(update_fields=["valid_until"])

        result = reauthorize_access_scope(authorization)

        self.assertIsInstance(result, AccessScopeDeniedDTO)

    def test_user_group_membership_change_revokes_group_authorization(self):
        user_group = UserGroup.objects.create(
            tenant=self.provider,
            name="Scope technicians",
            slug="scope-technicians",
        )
        GroupMembership.objects.create(user_group=user_group, membership=self.membership)
        grant = RoleGrant.objects.create(user_group=user_group, role=self.role)
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.tenant_a,
        )
        request = self._request(tenant_id=TenantId(self.tenant_a.pk))
        initial = resolve_access_scope(request)
        self.assertIsInstance(initial, AccessScopeResolvedDTO)
        authorization = ResolvedAccessAuthorizationDTO(
            actor=request.actor,
            request=request,
            initial_scope=initial.access_scope,
        )
        GroupMembership.objects.filter(user_group=user_group, membership=self.membership).delete()

        result = reauthorize_access_scope(authorization)

        self.assertIsInstance(result, AccessScopeDeniedDTO)

    def test_actor_change_changes_authorization_fingerprint_and_scope(self):
        self._grant(scope_type=RoleGrantScope.SCOPE_TENANT, tenant=self.tenant_a)
        first = resolve_access_scope(self._request(tenant_id=TenantId(self.tenant_a.pk)))
        self.assertIsInstance(first, AccessScopeResolvedDTO)

        other = User.objects.create_user(username="scope-other")
        other_membership = Membership.objects.create(user=other, tenant=self.provider)
        other_grant = RoleGrant.objects.create(membership=other_membership, role=self.role)
        RoleGrantScope.objects.create(
            role_grant=other_grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.tenant_a,
        )
        second = resolve_access_scope(self._request(actor=self._actor(other), tenant_id=TenantId(self.tenant_a.pk)))

        self.assertIsInstance(second, AccessScopeResolvedDTO)
        self.assertNotEqual(
            first.access_scope.authorization_revision,
            second.access_scope.authorization_revision,
        )
        self.assertNotEqual(
            first.access_scope.access_scope_fingerprint,
            second.access_scope.access_scope_fingerprint,
        )

    def test_fingerprint_changes_for_required_permission_and_selector(self):
        self._grant(scope_type=RoleGrantScope.SCOPE_TENANT, tenant=self.tenant_a)
        self._grant(scope_type=RoleGrantScope.SCOPE_TENANT, tenant=self.tenant_b)
        editor_role = Role.objects.create(
            tenant=self.provider,
            name="Scope editor",
            permissions=["assets.change_asset"],
        )
        editor_grant = RoleGrant.objects.create(
            membership=self.membership,
            role=editor_role,
            reason="Temporary specification test access",
            valid_until=timezone.now() + timedelta(hours=1),
        )
        RoleGrantScope.objects.create(
            role_grant=editor_grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.tenant_a,
        )
        first = resolve_access_scope(self._request(tenant_id=TenantId(self.tenant_a.pk)))
        second = resolve_access_scope(
            self._request(
                tenant_id=TenantId(self.tenant_a.pk),
                permission="assets.change_asset",
            )
        )
        third = resolve_access_scope(self._request(tenant_id=TenantId(self.tenant_b.pk)))

        self.assertIsInstance(first, AccessScopeResolvedDTO)
        self.assertIsInstance(second, AccessScopeResolvedDTO)
        self.assertIsInstance(third, AccessScopeResolvedDTO)
        self.assertNotEqual(first.access_scope.access_scope_fingerprint, second.access_scope.access_scope_fingerprint)
        self.assertNotEqual(first.access_scope.selector_fingerprint, third.access_scope.selector_fingerprint)

    def test_irrelevant_labels_do_not_change_fingerprints(self):
        self._grant(scope_type=RoleGrantScope.SCOPE_TENANT, tenant=self.tenant_a)
        request = self._request(tenant_id=TenantId(self.tenant_a.pk))
        before = resolve_access_scope(request)
        self.assertIsInstance(before, AccessScopeResolvedDTO)

        self.provider.name = "Renamed provider"
        self.provider.save(update_fields=["name"])
        self.role.name = "Renamed reader"
        self.role.save(update_fields=["name"])
        self.group.name = "Renamed group"
        self.group.save(update_fields=["name"])
        self.tenant_a.name = "Renamed tenant"
        self.tenant_a.save(update_fields=["name"])

        after = resolve_access_scope(request)

        self.assertIsInstance(after, AccessScopeResolvedDTO)
        self.assertEqual(before.access_scope.selector_fingerprint, after.access_scope.selector_fingerprint)
        self.assertEqual(before.access_scope.authorization_revision, after.access_scope.authorization_revision)
        self.assertEqual(before.access_scope.access_scope_fingerprint, after.access_scope.access_scope_fingerprint)

    def test_provider_query_errors_fail_closed(self):
        with patch(
            "organization.services.access_scope._tenant_scope.accessible_tenant_ids_with_expiry",
            side_effect=RuntimeError("provider unavailable"),
        ):
            result = resolve_access_scope(self._request(tenant_id=TenantId(self.tenant_a.pk)))

        self.assertEqual(result, AccessScopeDeniedDTO("denied", "OBJECT_UNAVAILABLE", ()))

    def test_provider_permission_map_is_used_instead_of_ambient_scope(self):
        self._grant(scope_type=RoleGrantScope.SCOPE_TENANT, tenant=self.tenant_a)
        request = self._request(tenant_id=TenantId(self.tenant_a.pk))

        with patch(
            "organization.services.access_scope._tenant_scope.build_accessible_tenant_permissions_map",
            wraps=build_accessible_tenant_permissions_map,
        ) as permission_map:
            result = resolve_access_scope(request)

        self.assertIsInstance(result, AccessScopeResolvedDTO)
        permission_map.assert_called_once()

    def test_inactive_actor_is_not_authorized(self):
        self._grant(scope_type=RoleGrantScope.SCOPE_TENANT, tenant=self.tenant_a)
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        result = resolve_access_scope(self._request(tenant_id=TenantId(self.tenant_a.pk)))

        self.assertIsInstance(result, AccessScopeDeniedDTO)
