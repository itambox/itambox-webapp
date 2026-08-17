"""Managed-tenant onboarding authorization and creator-access projection."""

from collections import defaultdict

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant
from organization.rbac import applicable_grants
from users.models import GroupMembership, UserGroup

User = get_user_model()

ADD_TENANT_PERMISSION = "organization.add_tenant"
MANAGED_SCOPE_TYPES = {
    RoleGrantScope.SCOPE_TENANT,
    RoleGrantScope.SCOPE_TENANT_GROUP,
    RoleGrantScope.SCOPE_ALL_MANAGED,
}


def _deny():
    raise PermissionDenied("Managed tenant onboarding authority is no longer valid.")


def _lock_tenants(*, provider_id, tenant_id):
    tenants = {
        tenant.pk: tenant
        for tenant in Tenant._base_manager.select_for_update().filter(pk__in=(provider_id, tenant_id)).order_by("pk")
    }
    provider = tenants.get(provider_id)
    tenant = tenants.get(tenant_id)
    if (
        provider is None
        or tenant is None
        or provider.pk == tenant.pk
        or provider.deleted_at is not None
        or not provider.is_provider
        or provider.managed_by_id is not None
        or tenant.deleted_at is not None
        or tenant.is_provider
        or tenant.managed_by_id != provider.pk
    ):
        _deny()
    return provider, tenant


def _lock_provider_groups(*, provider_id):
    # Canonical with users.api.scim.provider_services._sync_provider_group_members:
    # UserGroup -> Tenant -> User -> Membership -> GroupMembership -> grants.
    # Lock every provider group in PK order before requesting either Tenant lock.
    groups = list(UserGroup._base_manager.select_for_update().filter(tenant_id=provider_id).order_by("pk"))
    return {group.pk for group in groups if group.is_active and group.deleted_at is None}


def _lock_provider_principals(*, actor_id, provider, live_group_ids):
    actor = User._base_manager.select_for_update().filter(pk=actor_id, is_active=True).first()
    if actor is None or not actor.is_authenticated:
        _deny()

    membership = (
        Membership._base_manager.select_for_update()
        .filter(user_id=actor.pk, tenant_id=provider.pk, is_active=True)
        .order_by("pk")
        .first()
    )
    if membership is None:
        _deny()

    # This query deliberately runs after the provider group locks. If SCIM held
    # a group lock first, its committed insert/delete must be visible here.
    group_memberships = list(
        GroupMembership._base_manager.select_for_update()
        .filter(membership_id=membership.pk, user_group_id__in=live_group_ids)
        .order_by("pk")
    )
    current_group_ids = {group_membership.user_group_id for group_membership in group_memberships}
    return actor, membership, current_group_ids


def _lock_principal_grants(*, membership, group_ids):
    principal = Q(membership_id=membership.pk)
    if group_ids:
        principal |= Q(user_group_id__in=group_ids)
    locked_grants = list(RoleGrant._base_manager.select_for_update().filter(principal).order_by("pk"))

    role_ids = {grant.role_id for grant in locked_grants}
    list(Role._base_manager.select_for_update().filter(pk__in=role_ids).order_by("pk"))
    grant_ids = {grant.pk for grant in locked_grants}
    list(RoleGrantScope._base_manager.select_for_update().filter(role_grant_id__in=grant_ids).order_by("pk"))

    return list(
        RoleGrant._base_manager.filter(pk__in=grant_ids)
        .select_related("membership__tenant", "user_group__tenant", "role__tenant")
        .prefetch_related("scopes", "scopes__tenant", "scopes__tenant_group")
        .order_by("pk")
    )


def _role_has_add_tenant(role):
    try:
        permissions = set(role.permissions or ())
    except TypeError:
        return False
    return ADD_TENANT_PERMISSION in permissions


def _principal_kind(*, grant, membership, group_ids):
    if grant.membership_id == membership.pk and grant.user_group_id is None:
        return "direct"
    if grant.membership_id is None and grant.user_group_id in group_ids:
        return "group"
    return None


def _is_live_provider_authorizer(*, grant, provider, applicable_ids, now):
    if grant.pk not in applicable_ids:
        return False
    if grant.role.tenant_id != provider.pk or grant.role.deleted_at is not None:
        return False
    if grant.valid_until is not None and grant.valid_until <= now:
        return False
    if not _role_has_add_tenant(grant.role):
        return False
    return grant.covers_tenant(provider)


def _authorizing_grants(*, actor, provider, membership, group_ids, grants):
    if not actor.has_perm(ADD_TENANT_PERMISSION, obj=provider):
        _deny()

    applicable_ids = {grant.pk for grant in applicable_grants(actor)}
    now = timezone.now()
    authorizers = []
    for grant in grants:
        principal_kind = _principal_kind(grant=grant, membership=membership, group_ids=group_ids)
        if principal_kind is None:
            continue
        if not _is_live_provider_authorizer(
            grant=grant,
            provider=provider,
            applicable_ids=applicable_ids,
            now=now,
        ):
            continue
        if principal_kind == "direct" and grant.valid_until is None:
            _deny()
        authorizers.append(grant)

    if not authorizers:
        _deny()
    return authorizers


def _coalesce_direct_authorizers(authorizers):
    direct_by_principal_role = {}
    for source in authorizers:
        if source.membership_id is None:
            continue
        key = (source.membership_id, source.role_id)
        selected = direct_by_principal_role.get(key)
        if selected is None or (source.valid_until, source.pk) > (selected.valid_until, selected.pk):
            direct_by_principal_role[key] = source

    return [
        source
        for source in authorizers
        if source.membership_id is None
        or direct_by_principal_role[(source.membership_id, source.role_id)].pk == source.pk
    ]


def _onboarding_reason(source):
    return f"Managed tenant onboarding projected from provider role grant {source.pk}."


def _find_managed_aggregate(*, source, grants):
    managed_aggregates = []
    for grant in grants:
        if grant.user_group_id is not None or not grant.is_active:
            continue
        if grant.valid_until is None or grant.valid_until > source.valid_until:
            continue
        scope_types = {scope.scope_type for scope in grant.scopes.all()}
        if scope_types and scope_types <= MANAGED_SCOPE_TYPES:
            managed_aggregates.append(grant)

    if len(managed_aggregates) > 1:
        _deny()
    return managed_aggregates[0] if managed_aggregates else None


def _project_direct_grant(*, actor, source, tenant, grants):
    managed_grant = _find_managed_aggregate(source=source, grants=grants)
    if managed_grant is None:
        managed_grant = RoleGrant.objects.create(
            membership_id=source.membership_id,
            role_id=source.role_id,
            granted_by=actor,
            reason=_onboarding_reason(source),
            valid_until=source.valid_until,
        )
    RoleGrantScope.objects.get_or_create(
        role_grant=managed_grant,
        scope_type=RoleGrantScope.SCOPE_TENANT,
        tenant=tenant,
    )
    if managed_grant not in grants:
        grants.append(managed_grant)


def _project_group_grant(*, source, tenant):
    RoleGrantScope.objects.get_or_create(
        role_grant=source,
        scope_type=RoleGrantScope.SCOPE_TENANT,
        tenant=tenant,
    )


def onboard_managed_tenant(*, actor, provider_id, tenant):
    """Project every live provider grant that authorizes ``add_tenant``.

    The caller may already own a surrounding transaction (the UI create path
    does). The nested atomic block keeps direct service callers safe too.
    """
    if (
        actor is None
        or not getattr(actor, "is_authenticated", False)
        or getattr(actor, "is_superuser", False)
        or getattr(actor, "pk", None) is None
        or tenant.pk is None
    ):
        _deny()

    with transaction.atomic():
        live_group_ids = _lock_provider_groups(provider_id=provider_id)
        provider, tenant = _lock_tenants(provider_id=provider_id, tenant_id=tenant.pk)
        locked_actor, membership, group_ids = _lock_provider_principals(
            actor_id=actor.pk,
            provider=provider,
            live_group_ids=live_group_ids,
        )
        grants = _lock_principal_grants(membership=membership, group_ids=group_ids)
        authorizers = _authorizing_grants(
            actor=locked_actor,
            provider=provider,
            membership=membership,
            group_ids=group_ids,
            grants=grants,
        )
        authorizers = _coalesce_direct_authorizers(authorizers)

        grants_by_id = defaultdict(list)
        for grant in grants:
            grants_by_id[(grant.membership_id, grant.role_id)].append(grant)

        for source in authorizers:
            if source.membership_id is not None:
                _project_direct_grant(
                    actor=locked_actor,
                    source=source,
                    tenant=tenant,
                    grants=grants_by_id[(source.membership_id, source.role_id)],
                )
            else:
                _project_group_grant(source=source, tenant=tenant)

        return tenant
