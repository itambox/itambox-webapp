"""Trusted JIT SSO provisioning into canonical Membership and RoleGrant rows."""
from datetime import timedelta
import logging

from django.utils import timezone

from core.mfa import role_is_privileged

logger = logging.getLogger('itambox.auth.sso')

PRIVILEGED_ROLE_NAMES = {'Admin', 'Manager'}
PRIVILEGED_JIT_TTL = timedelta(days=1)


def _grant_metadata(role, source):
    if not role_is_privileged(role):
        return {'reason': '', 'valid_until': None}
    return {
        'reason': f'{source} group-claim provisioning',
        'valid_until': timezone.now() + PRIVILEGED_JIT_TTL,
    }


def _ensure_own_grant(membership, role, source):
    from organization.models import RoleGrant, RoleGrantScope

    grants = membership.role_grants.filter(
        role=role,
        scopes__scope_type=RoleGrantScope.SCOPE_OWN,
    ).distinct()
    grant = grants.first()
    metadata = _grant_metadata(role, source)
    if grant is None:
        grant = RoleGrant.objects.create(membership=membership, role=role, **metadata)
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )
    elif metadata['valid_until'] is not None:
        grant.reason = metadata['reason']
        grant.valid_until = metadata['valid_until']
        grant.save(update_fields=['reason', 'valid_until'])
    return grant


def provision_membership(user, tenant, db_role_name, permissions_for_role, source):
    from django.conf import settings
    from organization.models import Membership, Role, RoleGrantScope

    autocreate_privileged = getattr(
        settings,
        'ITAMBOX_SSO_AUTOCREATE_PRIVILEGED_ROLES',
        True,
    )
    is_privileged = db_role_name in PRIVILEGED_ROLE_NAMES
    username = getattr(user, 'username', user)
    tenant_slug = getattr(tenant, 'slug', tenant)

    role = Role._base_manager.filter(
        tenant=tenant,
        name=db_role_name,
        deleted_at__isnull=True,
    ).first()
    if role is None and is_privileged and not autocreate_privileged:
        logger.warning(
            "%s: refused auto-creation of privileged role '%s' for '%s' in '%s'; "
            "assigning Member instead.",
            source, db_role_name, username, tenant_slug,
        )
        role = _get_or_create_role(tenant, 'Member', permissions_for_role, source)
    elif role is None:
        role = _get_or_create_role(tenant, db_role_name, permissions_for_role, source)

    if role_is_privileged(role):
        logger.warning(
            "%s: refreshing temporary privileged role '%s' for '%s' in '%s'.",
            source, role.name, username, tenant_slug,
        )

    membership, _ = Membership.objects.get_or_create(user=user, tenant=tenant)
    for grant in membership.role_grants.filter(
        scopes__scope_type=RoleGrantScope.SCOPE_OWN,
    ).exclude(role=role).distinct():
        grant.scopes.filter(scope_type=RoleGrantScope.SCOPE_OWN).delete()
        if not grant.scopes.exists():
            grant.delete()
    _ensure_own_grant(membership, role, source)
    return role


def _get_or_create_role(tenant, name, permissions_for_role, source):
    from organization.models import Role

    role = Role._base_manager.filter(
        tenant=tenant,
        name=name,
        deleted_at__isnull=True,
    ).first()
    if role is None:
        role = Role._base_manager.create(
            tenant=tenant,
            name=name,
            description=f'Auto-provisioned {name} role via {source}',
            permissions=permissions_for_role(name),
        )
    return role


def provision_provider_membership(user, provider_tenant, role_name, source):
    """Provision provider identity; customer reach is always granted explicitly."""
    from organization.models import Membership, Role

    username = getattr(user, 'username', user)
    provider_slug = getattr(provider_tenant, 'slug', provider_tenant)
    if not getattr(provider_tenant, 'is_provider', False):
        logger.warning(
            "%s: staff mapping targets non-provider tenant '%s'; skipping '%s'.",
            source, provider_slug, username,
        )
        return None

    role = Role._base_manager.filter(
        tenant=provider_tenant,
        name=role_name,
        deleted_at__isnull=True,
    ).first()
    if role is None:
        logger.warning(
            "%s: provider role '%s' does not exist in '%s'; skipping '%s'.",
            source, role_name, provider_slug, username,
        )
        return None

    membership, _ = Membership.objects.get_or_create(user=user, tenant=provider_tenant)
    if not membership.is_active:
        membership.is_active = True
        membership.save(update_fields=['is_active'])

    logger.warning(
        "%s: provisioned provider membership for '%s' at '%s'; mapped role '%s' "
        "requires an explicit in-app grant and scope.",
        source, username, provider_slug, role_name,
    )
    return membership
