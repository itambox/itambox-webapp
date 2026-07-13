"""Phase-5 RBAC resolvers and legacy/new comparison boundary.

``legacy`` and ``compare`` return legacy authorization decisions. ``compare``
also evaluates the RoleGrant model and logs every disagreement. ``new`` is the
explicit cutover mode and must not be enabled until the exhaustive comparison
command reports zero differences.
"""
import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import F, Q
from django.utils import timezone

from organization.access import get_descendant_tenant_group_ids, legacy_accessible_tenant_ids
from organization.models import Role, RoleAssignment, RoleGrant, RoleGrantScope, Tenant

logger = logging.getLogger('itambox.auth.rbac')

MODE_LEGACY = 'legacy'
MODE_COMPARE = 'compare'
MODE_NEW = 'new'
VALID_MODES = {MODE_LEGACY, MODE_COMPARE, MODE_NEW}


def resolver_mode():
    mode = getattr(settings, 'RBAC_RESOLVER_MODE', MODE_LEGACY).lower()
    if mode not in VALID_MODES:
        raise ImproperlyConfigured(
            f'RBAC_RESOLVER_MODE must be one of {sorted(VALID_MODES)}, got {mode!r}.'
        )
    return mode


def legacy_effective_permissions(user, tenant):
    """Frozen copy of the pre-phase-5 permission union."""
    permissions = set()
    for permission_list in Role._base_manager.filter(
        deleted_at__isnull=True,
        assignments__reach=RoleAssignment.REACH_OWN,
        assignments__membership__user=user,
        assignments__membership__tenant=tenant,
        assignments__membership__is_active=True,
    ).values_list('permissions', flat=True):
        permissions.update(permission_list or [])

    for permission_list in Role._base_manager.filter(
        tenant=tenant,
        deleted_at__isnull=True,
        user_groups__members=user,
        user_groups__is_active=True,
        user_groups__deleted_at__isnull=True,
    ).values_list('permissions', flat=True):
        permissions.update(permission_list or [])

    if tenant.managed_by_id:
        assignments = RoleAssignment.objects.filter(
            reach=RoleAssignment.REACH_MANAGED,
            membership__user=user,
            membership__tenant_id=tenant.managed_by_id,
            membership__is_active=True,
        ).select_related('role', 'scope_group', 'membership')
        for assignment in assignments:
            if assignment.role.deleted_at is None and assignment.covers_tenant(tenant):
                permissions.update(assignment.role.permissions or [])
    return frozenset(permissions)


def applicable_new_grants(user):
    """Return live RoleGrants whose direct/group principal belongs to ``user``."""
    now = timezone.now()
    principal = (
        Q(membership__user=user, membership__is_active=True)
        | Q(
            user_group__tenant__isnull=False,
            user_group__is_active=True,
            user_group__deleted_at__isnull=True,
            user_group__group_memberships__membership__user=user,
            user_group__group_memberships__membership__is_active=True,
            user_group__group_memberships__membership__tenant_id=F('user_group__tenant_id'),
        )
    )
    return list(
        RoleGrant.objects.filter(
            principal,
            role__deleted_at__isnull=True,
        ).filter(
            Q(valid_until__isnull=True) | Q(valid_until__gt=now)
        ).select_related(
            'membership__tenant',
            'user_group__tenant',
            'role__tenant',
        ).prefetch_related(
            'scopes',
            'scopes__tenant',
            'scopes__tenant_group',
        ).distinct()
    )


def new_effective_permissions(user, tenant):
    permissions = set()
    for grant in applicable_new_grants(user):
        if grant.covers_tenant(tenant):
            permissions.update(grant.role.permissions or [])
    return frozenset(permissions)


def _log_permission_difference(user, tenant, legacy, new):
    logger.warning(
        'RBAC resolver disagreement user_id=%s tenant_id=%s legacy_only=%s new_only=%s',
        user.pk,
        tenant.pk,
        sorted(legacy - new),
        sorted(new - legacy),
    )


def resolve_effective_permissions(user, tenant):
    mode = resolver_mode()
    if mode == MODE_NEW:
        return new_effective_permissions(user, tenant)
    legacy = legacy_effective_permissions(user, tenant)
    if mode == MODE_COMPARE:
        new = new_effective_permissions(user, tenant)
        if legacy != new:
            _log_permission_difference(user, tenant, legacy, new)
    return legacy


def new_accessible_tenant_ids(user):
    """Tenant ids reachable through memberships or live RoleGrant scopes."""
    live_tenants = Tenant._base_manager.filter(deleted_at__isnull=True)
    tenant_ids = set(
        live_tenants.filter(
            memberships__user=user,
            memberships__is_active=True,
        ).values_list('pk', flat=True)
    )

    for grant in applicable_new_grants(user):
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
                group_ids = get_descendant_tenant_group_ids(scope.tenant_group_id)
                tenant_ids.update(
                    live_tenants.filter(
                        managed_by_id=grant.role.tenant_id,
                        group_id__in=group_ids,
                    ).values_list('pk', flat=True)
                )
    return tenant_ids


def resolve_accessible_tenant_ids(user):
    mode = resolver_mode()
    if mode == MODE_NEW:
        return new_accessible_tenant_ids(user)
    legacy = legacy_accessible_tenant_ids(user)
    if mode == MODE_COMPARE:
        new = new_accessible_tenant_ids(user)
        if legacy != new:
            logger.warning(
                'RBAC accessible-tenant disagreement user_id=%s legacy_only=%s new_only=%s',
                user.pk,
                sorted(legacy - new),
                sorted(new - legacy),
            )
    return legacy
