"""Canonical tenant, RBAC, and explicitly shared-resource access helpers."""


def get_descendant_tenant_group_ids(group_id, live_only=False):
    if group_id is None:
        return set()
    # inline import: avoids organization model import during app initialization.
    from organization.models import TenantGroup

    if live_only and not TenantGroup._base_manager.filter(
        pk=group_id,
        deleted_at__isnull=True,
    ).exists():
        return set()

    ids = {group_id}
    frontier = [group_id]
    while frontier:
        children_qs = TenantGroup._base_manager.filter(
            parent_id__in=frontier,
        ).exclude(pk__in=ids)
        if live_only:
            children_qs = children_qs.filter(deleted_at__isnull=True)
        children = list(children_qs.values_list('pk', flat=True))
        if not children:
            break
        ids.update(children)
        frontier = children
    return ids


def get_ancestor_tenant_group_ids(group_id, live_only=False):
    if group_id is None:
        return set()
    # inline import: avoids organization model import during app initialization.
    from organization.models import TenantGroup

    seen = set()
    node = group_id
    while node is not None and node not in seen:
        row = (
            TenantGroup._base_manager.filter(pk=node)
            .values('parent_id', 'deleted_at')
            .first()
        )
        if row is None or (live_only and row['deleted_at'] is not None):
            break
        seen.add(node)
        node = row['parent_id']
    return seen


def shared_resource_ids(model, tenant):
    """Pool ids of ``model`` explicitly shared to ``tenant``."""
    # inline imports: avoid AppRegistryNotReady during app initialization.
    from django.contrib.contenttypes.models import ContentType
    from django.db.models import Q
    from organization.models import TenantResourceGrant

    if tenant is None:
        return TenantResourceGrant.objects.none().values_list('resource_id', flat=True)
    content_type = ContentType.objects.get_for_model(model)
    grantee = Q(grantee_tenant_id=tenant.pk)
    ancestor_group_ids = get_ancestor_tenant_group_ids(
        tenant.group_id,
        live_only=True,
    )
    if ancestor_group_ids:
        grantee |= Q(grantee_tenant_group_id__in=ancestor_group_ids)
    return (
        TenantResourceGrant.objects.filter(resource_type=content_type)
        .filter(grantee)
        .values_list('resource_id', flat=True)
    )


def accessible_tenant_ids(user):
    if user is None or not getattr(user, 'is_authenticated', False):
        return set()
    # inline import: avoids organization.access <-> organization.rbac at load time.
    from organization.rbac import resolve_accessible_tenant_ids
    return resolve_accessible_tenant_ids(user)


def managed_accessible_tenant_ids(user):
    if user is None or not getattr(user, 'is_authenticated', False):
        return set()
    # inline import: avoids organization.access <-> organization.rbac at load time.
    from organization.rbac import applicable_grants

    tenant_ids = set()
    for grant in applicable_grants(user):
        tenant_ids.update(grant.scoped_tenant_ids())
    return tenant_ids


def tenant_access_report(tenant, external_only=False):
    """Return users who can access ``tenant`` with native grant provenance."""
    # inline imports: keep this model-heavy module safe during app setup.
    from django.db.models import Q
    from django.utils import timezone
    from organization.models import Membership, RoleGrant

    user_data = {}

    def entry_for(user):
        if user.pk not in user_data:
            user_data[user.pk] = {
                'user': user,
                'sources': set(),
                'groups': set(),
                'permissions': set(),
            }
        return user_data[user.pk]

    if not external_only:
        memberships = Membership.objects.filter(
            tenant=tenant,
            is_active=True,
        ).select_related('user')
        for membership in memberships:
            entry_for(membership.user)['sources'].add('membership')

    grants = (
        RoleGrant.objects.filter(role__deleted_at__isnull=True)
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=timezone.now()))
        .select_related(
            'membership__user',
            'membership__tenant',
            'user_group__tenant',
            'role__tenant',
        )
        .prefetch_related(
            'scopes',
            'scopes__tenant',
            'scopes__tenant_group',
            'user_group__group_memberships__membership__user',
        )
    )
    for grant in grants:
        if not grant.covers_tenant(tenant):
            continue
        if grant.membership_id:
            if not grant.membership.is_active:
                continue
            entry = entry_for(grant.membership.user)
            source = 'membership' if grant.membership.tenant_id == tenant.pk else 'managed'
            entry['sources'].add(source)
            entry['permissions'].update(grant.role.permissions or [])
            continue

        group = grant.user_group
        if not group.is_active or group.deleted_at is not None:
            continue
        for group_membership in group.group_memberships.all():
            membership = group_membership.membership
            if not membership.is_active or membership.tenant_id != group.tenant_id:
                continue
            entry = entry_for(membership.user)
            entry['sources'].add('group')
            if group.tenant_id != tenant.pk:
                entry['sources'].add('managed')
            entry['groups'].add(group.name)
            entry['permissions'].update(grant.role.permissions or [])

    if external_only:
        local_user_ids = set(
            Membership.objects.filter(tenant=tenant).values_list('user_id', flat=True)
        )
        user_data = {
            pk: data for pk, data in user_data.items() if pk not in local_user_ids
        }

    report = []
    for data in user_data.values():
        user = data['user']
        report.append({
            'user': user,
            'sources': sorted(data['sources']),
            'groups': sorted(data['groups']),
            'permissions': sorted(data['permissions']),
            'inactive': not (user.is_active and getattr(user, 'can_login', True)),
        })
    report.sort(key=lambda row: (row['user'].username or '').lower())
    return report
