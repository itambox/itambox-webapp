"""Canonical RoleGrant-based authorization resolution."""
from django.db.models import F, Q
from django.utils import timezone

from organization.access import get_descendant_tenant_group_ids
from organization.models import RoleGrant, RoleGrantScope, Tenant


def applicable_grants(user):
    """Return every live direct/group grant whose principal belongs to ``user``.

    Request-local memoization keyed to the user instance mirrors
    ``accessible_tenant_ids_with_expiry`` (organization.access): this query runs
    several joins and is called once per distinct tenant per request, so an
    all-accessible scope re-ran the full grant walk for every accessible tenant
    (issue #56). The shared authorization-cache generation is consulted first,
    so the memo can never serve a write-invalidated grant set; a cache-backend
    outage makes ``synchronize_authorization_cache`` fail open to a fresh local
    version, forcing a recompute rather than serving a stale set.
    """
    can_cache = hasattr(user, '__dict__')
    if can_cache:
        # inline import: avoids an organization.rbac -> core.auth import cycle at
        # load (core.auth resolves permissions through organization.rbac).
        from core.auth.cache import synchronize_authorization_cache
        synchronize_authorization_cache(user)
        cached = user.__dict__.get('_applicable_grants')
        if cached is not None:
            return cached
    now = timezone.now()
    principal = (
        Q(membership__user=user, membership__is_active=True)
        | Q(
            user_group__is_active=True,
            user_group__deleted_at__isnull=True,
            user_group__group_memberships__membership__user=user,
            user_group__group_memberships__membership__is_active=True,
            user_group__group_memberships__membership__tenant_id=F('user_group__tenant_id'),
        )
    )
    grants = list(
        RoleGrant.objects.filter(principal, role__deleted_at__isnull=True)
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=now))
        .select_related(
            'membership__tenant',
            'user_group__tenant',
            'role__tenant',
        )
        .prefetch_related('scopes', 'scopes__tenant', 'scopes__tenant_group')
        .distinct()
    )
    if can_cache:
        user.__dict__['_applicable_grants'] = grants
    return grants


def effective_permissions_with_expiry(user, tenant):
    """Return permissions plus the first expiry that can change that result."""
    permissions = set()
    valid_until = None
    for grant in applicable_grants(user):
        if grant.covers_tenant(tenant):
            permissions.update(grant.role.permissions or [])
            if grant.valid_until is not None and (
                valid_until is None or grant.valid_until < valid_until
            ):
                valid_until = grant.valid_until
    return frozenset(permissions), valid_until


def effective_permissions(user, tenant):
    return effective_permissions_with_expiry(user, tenant)[0]


def _scope_contribution(scope, grant, owner_id, live_tenants):
    """Tenant ids contributed by a single grant scope, plus whether the grant
    should count toward the accessible-set expiry (only when it actually
    contributed an id).
    """
    if scope.scope_type == RoleGrantScope.SCOPE_OWN:
        owner = live_tenants.filter(pk=owner_id).first()
        if owner is not None and grant.covers_tenant(owner):
            return {owner.pk}, True
        return set(), False

    if scope.scope_type == RoleGrantScope.SCOPE_TENANT:
        target = live_tenants.filter(pk=scope.tenant_id).first()
        if target is not None and grant.covers_tenant(target):
            return {target.pk}, True
        return set(), False

    if scope.scope_type == RoleGrantScope.SCOPE_ALL_MANAGED:
        if owner_id != grant.role.tenant_id or not grant.role.tenant.is_provider:
            return set(), False
        managed_ids = set(
            live_tenants.filter(managed_by_id=grant.role.tenant_id)
            .values_list('pk', flat=True)
        )
        return managed_ids, bool(managed_ids)

    if scope.scope_type == RoleGrantScope.SCOPE_TENANT_GROUP:
        if owner_id != grant.role.tenant_id or not grant.role.tenant.is_provider:
            return set(), False
        managed_ids = set(
            live_tenants.filter(
                managed_by_id=grant.role.tenant_id,
                group_id__in=get_descendant_tenant_group_ids(
                    scope.tenant_group_id,
                    live_only=True,
                ),
            ).values_list('pk', flat=True)
        )
        return managed_ids, bool(managed_ids)

    return set(), False


def accessible_tenant_ids_with_expiry(user):
    """Tenant ids reachable through active memberships or additive grant scopes,
    plus the earliest ``valid_until`` among the grants that contributed one.

    ``RoleGrant.valid_until`` expires purely by the clock — no save/signal fires
    when a grant lapses — so a memo of the accessible set must know when it can
    no longer be trusted. Direct memberships carry no expiry of their own (only
    ``is_active``, which IS signal-backed), so only grant-contributed ids affect
    the returned expiry.
    """
    live_tenants = Tenant._base_manager.filter(deleted_at__isnull=True)
    tenant_ids = set(
        live_tenants.filter(
            memberships__user=user,
            memberships__is_active=True,
        ).values_list('pk', flat=True)
    )
    valid_until = None

    def _note_expiry(grant):
        nonlocal valid_until
        if grant.valid_until is not None and (
            valid_until is None or grant.valid_until < valid_until
        ):
            valid_until = grant.valid_until

    for grant in applicable_grants(user):
        owner_id = grant.principal_tenant_id
        for scope in grant.scopes.all():
            ids, counts_toward_expiry = _scope_contribution(scope, grant, owner_id, live_tenants)
            if ids:
                tenant_ids.update(ids)
            if counts_toward_expiry:
                _note_expiry(grant)
    return frozenset(tenant_ids), valid_until


def accessible_tenant_ids(user):
    """Tenant ids reachable through active memberships or additive grant scopes."""
    return set(accessible_tenant_ids_with_expiry(user)[0])


# Stable public names used by the auth backend and tenant-scoping layer.
resolve_effective_permissions = effective_permissions
resolve_effective_permissions_with_expiry = effective_permissions_with_expiry
resolve_accessible_tenant_ids = accessible_tenant_ids
resolve_accessible_tenant_ids_with_expiry = accessible_tenant_ids_with_expiry
