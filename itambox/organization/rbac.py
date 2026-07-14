"""Canonical RoleGrant-based authorization resolution."""
from django.db.models import F, Q
from django.utils import timezone

from organization.access import get_descendant_tenant_group_ids
from organization.models import RoleGrant, RoleGrantScope, Tenant


def applicable_grants(user):
    """Return every live direct/group grant whose principal belongs to ``user``."""
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
    return list(
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


def accessible_tenant_ids(user):
    """Tenant ids reachable through active memberships or additive grant scopes."""
    live_tenants = Tenant._base_manager.filter(deleted_at__isnull=True)
    tenant_ids = set(
        live_tenants.filter(
            memberships__user=user,
            memberships__is_active=True,
        ).values_list('pk', flat=True)
    )

    for grant in applicable_grants(user):
        owner_id = grant.principal_tenant_id
        for scope in grant.scopes.all():
            if scope.scope_type == RoleGrantScope.SCOPE_OWN:
                owner = live_tenants.filter(pk=owner_id).first()
                if owner is not None and grant.covers_tenant(owner):
                    tenant_ids.add(owner.pk)
            elif scope.scope_type == RoleGrantScope.SCOPE_TENANT:
                target = live_tenants.filter(pk=scope.tenant_id).first()
                if target is not None and grant.covers_tenant(target):
                    tenant_ids.add(target.pk)
            elif scope.scope_type == RoleGrantScope.SCOPE_ALL_MANAGED:
                if owner_id != grant.role.tenant_id or not grant.role.tenant.is_provider:
                    continue
                tenant_ids.update(
                    live_tenants.filter(managed_by_id=grant.role.tenant_id)
                    .values_list('pk', flat=True)
                )
            elif scope.scope_type == RoleGrantScope.SCOPE_TENANT_GROUP:
                if owner_id != grant.role.tenant_id or not grant.role.tenant.is_provider:
                    continue
                tenant_ids.update(
                    live_tenants.filter(
                        managed_by_id=grant.role.tenant_id,
                        group_id__in=get_descendant_tenant_group_ids(
                            scope.tenant_group_id,
                            live_only=True,
                        ),
                    ).values_list('pk', flat=True)
                )
    return tenant_ids


# Stable public names used by the auth backend and tenant-scoping layer.
resolve_effective_permissions = effective_permissions
resolve_effective_permissions_with_expiry = effective_permissions_with_expiry
resolve_accessible_tenant_ids = accessible_tenant_ids
