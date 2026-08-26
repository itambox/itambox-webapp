"""Privilege-escalation guards for canonical RoleGrant writes.

The guard policy intentionally lives in the organization service layer rather
than in the framework authentication layer. The permission helper below
retains the former authorization backend's per-tenant cache contract while
delegating cache misses to the canonical tenant-scope resolver.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol, cast

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core import tenant_scope
from core.authorization_cache import synchronize_authorization_cache
from core.tenant_scope import get_descendant_tenant_group_ids
from organization.models import Role, RoleGrant, RoleGrantScope, Tenant
from organization.rbac import applicable_grants


class _AuthorizationUser(Protocol):
    pk: int

    def has_perm(self, permission: str, obj: Tenant | None = None) -> bool:
        """Return whether this principal holds ``permission`` for ``obj``."""


class _GrantRelation(Protocol):
    def select_related(self, *fields: str) -> _GrantRelation:
        """Return a query relation with the requested joins."""

    def prefetch_related(self, *lookups: str) -> Iterable[RoleGrant]:
        """Return the relation with the requested prefetched children."""


class _UserGroup(Protocol):
    pk: int
    is_active: bool
    tenant: Tenant
    role_grants: _GrantRelation


_PermissionCache = tuple[frozenset[str], datetime | None]


def _effective_permissions_for_granting_user(
    granting_user: _AuthorizationUser,
    tenant: Tenant,
) -> frozenset[str]:
    """Preserve the former authorization backend's tenant-permission cache contract."""
    synchronize_authorization_cache(granting_user)
    cache_key = f"_perms_tenant_{tenant.pk}"
    if hasattr(granting_user, cache_key):
        permissions, valid_until = cast(_PermissionCache, getattr(granting_user, cache_key))
        if valid_until is None or valid_until > timezone.now():
            return permissions

    permissions, valid_until = cast(
        _PermissionCache,
        tenant_scope.resolve_effective_permissions_with_expiry(granting_user, tenant),
    )
    setattr(granting_user, cache_key, (permissions, valid_until))
    return permissions


def validate_permission_grant(granting_user, permissions, tenant):
    # type: (_AuthorizationUser | None, Iterable[str] | None, Tenant | None) -> None
    """Enforce "you cannot grant permissions you do not hold"."""
    if granting_user is None or getattr(granting_user, "is_superuser", False):
        return
    requested = set(permissions or [])
    if not requested:
        return
    held = _effective_permissions_for_granting_user(granting_user, tenant) if tenant is not None else frozenset()
    escalated = sorted(requested - set(held))
    if escalated:
        raise ValidationError(
            _("Privilege escalation detected: you cannot grant permissions you do not hold: %(perms)s")
            % {"perms": ", ".join(escalated)}
        )


def _has_managed_grant_permission(granting_user: _AuthorizationUser, principal_tenant: Tenant) -> bool:
    """Return whether the actor may delegate managed-tenant reach."""
    return granting_user.has_perm("organization.add_rolegrant", obj=principal_tenant) or granting_user.has_perm(
        "organization.change_rolegrant",
        obj=principal_tenant,
    )


def _managed_grant_authority(
    granting_user: _AuthorizationUser,
    principal_tenant: Tenant,
) -> tuple[bool, set[str], set[int]]:
    """Collect managed-scope authority without changing grant query order."""
    own_ids: set[int] = set()
    all_managed_permissions: set[str] = set()
    has_all_managed_scope = False
    for grant in applicable_grants(granting_user):
        if grant.principal_tenant_id != principal_tenant.pk:
            continue
        has_valid_all_managed_scope = (
            grant.role.tenant_id == principal_tenant.pk
            and principal_tenant.is_provider
            and any(scope.scope_type == RoleGrantScope.SCOPE_ALL_MANAGED for scope in grant.scopes.all())
        )
        if has_valid_all_managed_scope:
            has_all_managed_scope = True
            all_managed_permissions.update(grant.role.permissions or [])
        own_ids.update(grant.scoped_tenant_ids())
    return has_all_managed_scope, all_managed_permissions, own_ids


def _validate_dynamic_managed_scope(
    requested_permissions: set[str],
    has_all_managed_scope: bool,
    all_managed_permissions: set[str],
) -> None:
    """Validate all-managed and tenant-group delegation authority."""
    missing_permissions = sorted(requested_permissions - all_managed_permissions)
    if not has_all_managed_scope or missing_permissions:
        detail = (
            _(" Missing permissions: %(perms)s") % {"perms": ", ".join(missing_permissions)}
            if missing_permissions
            else ""
        )
        raise ValidationError(
            _(
                "You cannot grant a dynamic managed-tenant scope unless you "
                "hold equivalent permission authority across all managed tenants."
            )
            + detail
        )


def _validate_explicit_managed_scope(
    granting_user: _AuthorizationUser,
    requested_permissions: set[str],
    own_ids: set[int],
    requested_tenant_ids: Iterable[int] | None,
) -> None:
    """Validate concrete managed targets and their complete permission sets."""
    if requested_tenant_ids is None:
        raise ValidationError(_("You cannot grant reach into all managed tenants; your own reach is narrower."))
    requested_tenant_ids = set(requested_tenant_ids)
    missing = requested_tenant_ids - own_ids
    if missing:
        raise ValidationError(_("You cannot grant reach into tenants outside your own reach."))

    # Reach alone is not authority to delegate the permissions carried by a
    # role. Prove the actor holds the complete requested permission set inside
    # every concrete target tenant; otherwise an own-tenant Admin grant could be
    # combined with an unrelated read-only/empty coverage grant to manufacture
    # Admin access in that customer.
    targets = list(
        Tenant._base_manager.filter(
            pk__in=requested_tenant_ids,
            deleted_at__isnull=True,
        )
    )
    if len(targets) != len(requested_tenant_ids):
        raise ValidationError(_("You cannot grant reach into tenants outside your own reach."))
    for target in targets:
        validate_permission_grant(granting_user, requested_permissions, target)


def _validate_managed_role_grant(
    granting_user: _AuthorizationUser,
    role: Role,
    principal_tenant: Tenant,
    *,
    scope_type: str,
    requested_tenant_ids: Iterable[int] | None,
) -> None:
    """Validate the managed portion of one role grant."""
    if not _has_managed_grant_permission(granting_user, principal_tenant):
        raise ValidationError(_("You are not allowed to grant reach into managed tenants."))

    requested_permissions = set(role.permissions or [])
    has_all_managed_scope, all_managed_permissions, own_ids = _managed_grant_authority(
        granting_user,
        principal_tenant,
    )
    if scope_type in (
        RoleGrantScope.SCOPE_TENANT_GROUP,
        RoleGrantScope.SCOPE_ALL_MANAGED,
    ):
        _validate_dynamic_managed_scope(
            requested_permissions,
            has_all_managed_scope,
            all_managed_permissions,
        )
        return
    _validate_explicit_managed_scope(
        granting_user,
        requested_permissions,
        own_ids,
        requested_tenant_ids,
    )


def validate_role_grant(
    granting_user,
    role,
    principal_tenant,
    *,
    scope_type="own",
    requested_tenant_ids=None,
):
    # type: (_AuthorizationUser | None, Role, Tenant, str, Iterable[int] | None) -> None
    """Validate permission content and managed coverage for one RoleGrant aggregate."""
    if granting_user is None or getattr(granting_user, "is_superuser", False):
        return

    validate_permission_grant(
        granting_user,
        getattr(role, "permissions", None) or [],
        principal_tenant,
    )
    if scope_type == "own":
        return
    _validate_managed_role_grant(
        granting_user,
        role,
        principal_tenant,
        scope_type=scope_type,
        requested_tenant_ids=requested_tenant_ids,
    )


def _live_grant_principal_tenant(
    grant: RoleGrant,
    *,
    restoring_user_group_id: int | None,
) -> Tenant | None:
    """Return a live grant principal's tenant, or ``None`` for inert history."""
    if not grant.is_active:
        return None
    if grant.membership_id:
        membership = grant.membership
        if membership is None or not membership.is_active:
            return None
        return membership.tenant
    if not grant.user_group_id:
        return None
    user_group = grant.user_group
    if user_group is None or (
        not user_group.is_active or (user_group.deleted_at is not None and user_group.pk != restoring_user_group_id)
    ):
        return None
    return user_group.tenant


def _role_restore_dependencies_are_live(
    principal_tenant: Tenant,
    role: Role,
    restoring_role_id: int | None,
) -> bool:
    """Check the live tenant/role dependencies of a retained grant."""
    if principal_tenant.deleted_at is not None or role.tenant.deleted_at is not None:
        return False
    if role.deleted_at is not None and role.pk != restoring_role_id:
        return False
    return True


def _has_live_tenant_group_scope(scope: RoleGrantScope, role: Role) -> bool:
    """Return whether a tenant-group scope still reaches a live managed tenant."""
    target_group = scope.tenant_group
    if target_group is None or target_group.deleted_at is not None:
        return False
    return Tenant._base_manager.filter(
        managed_by_id=role.tenant_id,
        group_id__in=get_descendant_tenant_group_ids(
            target_group.pk,
            live_only=True,
        ),
        deleted_at__isnull=True,
    ).exists()


def _classify_live_role_grant_scopes(
    grant: RoleGrant,
    role: Role,
    principal_tenant: Tenant,
) -> tuple[bool, set[int], bool, bool]:
    """Classify retained scopes while preserving their original evaluation order."""
    has_own = False
    explicit_tenant_ids: set[int] = set()
    has_live_group_scope = False
    has_all_managed = False
    managed_shape_is_live = principal_tenant.pk == role.tenant_id and role.tenant.is_provider

    for scope in grant.scopes.all():
        if scope.scope_type == RoleGrantScope.SCOPE_OWN:
            has_own = role.tenant_id == principal_tenant.pk or bool(
                grant.membership_id
                and role.shared_with_managed
                and role.tenant.is_provider
                and principal_tenant.managed_by_id == role.tenant_id
            )
        elif scope.scope_type == RoleGrantScope.SCOPE_ALL_MANAGED and managed_shape_is_live:
            # This remains a live dynamic capability even before the provider
            # has its first customer; restoring it must prove all-managed authority.
            has_all_managed = True
        elif scope.scope_type == RoleGrantScope.SCOPE_TENANT and managed_shape_is_live:
            target = scope.tenant
            if target is not None and target.deleted_at is None and target.managed_by_id == role.tenant_id:
                explicit_tenant_ids.add(target.pk)
        elif scope.scope_type == RoleGrantScope.SCOPE_TENANT_GROUP and managed_shape_is_live:
            has_live_group_scope = has_live_group_scope or _has_live_tenant_group_scope(scope, role)

    return has_own, explicit_tenant_ids, has_live_group_scope, has_all_managed


def _scope_request_from_classification(
    grant: RoleGrant,
    has_own: bool,
    explicit_tenant_ids: set[int],
    has_live_group_scope: bool,
    has_all_managed: bool,
) -> tuple[str, set[int] | None] | None:
    """Convert a retained-scope classification to canonical validator inputs."""
    if has_all_managed:
        return "all_managed", None
    if has_live_group_scope:
        return "tenant_group", grant.scoped_tenant_ids()
    if explicit_tenant_ids:
        return "tenant", explicit_tenant_ids
    if has_own:
        return "own", None
    return None


def _live_role_grant_scope_request(
    grant,
    *,
    restoring_role_id=None,
    restoring_user_group_id=None,
):
    # type: (RoleGrant, int | None, int | None) -> tuple[str, set[int] | None] | None
    """Return canonical validation args only when ``grant`` can become effective."""
    principal_tenant = _live_grant_principal_tenant(
        grant,
        restoring_user_group_id=restoring_user_group_id,
    )
    if principal_tenant is None:
        return None
    role = grant.role
    if not _role_restore_dependencies_are_live(principal_tenant, role, restoring_role_id):
        return None
    return _scope_request_from_classification(
        grant,
        *_classify_live_role_grant_scopes(grant, role, principal_tenant),
    )


def validate_role_reactivation_grants(granting_user, role):
    # type: (_AuthorizationUser | None, Role) -> None
    """Validate retained grants that restoring ``role`` would make effective."""
    if granting_user is None or getattr(granting_user, "is_superuser", False):
        return

    errors = []
    grants = role.role_grants.select_related(
        "membership__tenant",
        "user_group__tenant",
        "role__tenant",
    ).prefetch_related(
        "scopes",
        "scopes__tenant",
        "scopes__tenant_group",
    )
    for grant in grants:
        request = _live_role_grant_scope_request(
            grant,
            restoring_role_id=role.pk,
        )
        if request is None:
            continue
        scope_type, requested_tenant_ids = request
        try:
            validate_role_grant(
                granting_user,
                role,
                grant.tenant,
                scope_type=scope_type,
                requested_tenant_ids=requested_tenant_ids,
            )
        except ValidationError as exc:
            errors.extend(exc.messages)
    if errors:
        raise ValidationError(errors)


def validate_group_membership_grant(granting_user, group):
    # type: (_AuthorizationUser | None, object) -> None
    """Validate every live permission and scope inherited by joining ``group``."""
    if granting_user is None or getattr(granting_user, "is_superuser", False):
        return
    typed_group = cast(_UserGroup, group)
    if not typed_group.is_active:
        return

    errors: list[str] = []
    grants = typed_group.role_grants.select_related(
        "membership__tenant",
        "role__tenant",
        "user_group__tenant",
    ).prefetch_related(
        "scopes",
        "scopes__tenant",
        "scopes__tenant_group",
    )
    for grant in grants:
        request = _live_role_grant_scope_request(
            grant,
            restoring_user_group_id=typed_group.pk,
        )
        if request is None:
            continue
        scope_type, requested_tenant_ids = request
        try:
            validate_role_grant(
                granting_user,
                grant.role,
                typed_group.tenant,
                scope_type=scope_type,
                requested_tenant_ids=requested_tenant_ids,
            )
        except ValidationError as exc:
            errors.extend(exc.messages)
    if errors:
        raise ValidationError(errors)
