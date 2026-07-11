"""Cross-tenant access helpers (unified RBAC, one container type).

A (non-superuser) user's accessible tenants are the union of:

  1. their active ``Membership`` rows,
  2. tenants owning a ``Role`` granted to them via an active, non-deleted ``UserGroup``, and
  3. managed tenants reachable through managed-reach ``RoleAssignment`` rows on their
     active memberships at managing (``is_provider``) tenants.

This is the single source of truth for "which tenants may this user enter", used by
``TenantMiddleware`` and the tenant switcher. Permission *content* within a tenant is
resolved separately by :class:`core.auth.MembershipBackend`.
"""


def get_descendant_tenant_group_ids(group_id):
    """Return ``group_id`` plus the ids of all its descendant TenantGroups (inclusive)."""
    if group_id is None:
        return set()
    # inline import: avoid AppRegistryNotReady / a core<->organization cycle at load
    from organization.models import TenantGroup

    ids = {group_id}
    frontier = [group_id]
    while frontier:
        children = list(
            TenantGroup._base_manager.filter(parent_id__in=frontier)
            .exclude(pk__in=ids)
            .values_list('pk', flat=True)
        )
        if not children:
            break
        ids.update(children)
        frontier = children
    return ids


def managed_accessible_tenant_ids(user):
    """Managed-tenant ids reachable via the user's managed-reach assignments (step 3).

    The per-scope resolution lives on the model as ``RoleAssignment.scoped_tenant_ids``
    (the canonical helper); this function just unions it across the user's active
    managed-reach assignments.
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return set()
    from organization.models import RoleAssignment

    ids = set()
    assignments = RoleAssignment.objects.filter(
        reach=RoleAssignment.REACH_MANAGED,
        membership__user=user,
        membership__is_active=True,
    ).select_related('membership', 'scope_group')
    for assignment in assignments:
        ids |= assignment.scoped_tenant_ids()
    return ids


def accessible_tenant_ids(user):
    """Return the set of tenant IDs ``user`` may access."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return set()
    from organization.models import Membership, Role

    ids = set(
        Membership.objects.filter(
            user=user, is_active=True,
        ).values_list('tenant_id', flat=True)
    )
    # Roles attached via UserGroups grant access to the role's owning tenant.
    ids |= set(
        Role._base_manager.filter(
            deleted_at__isnull=True,
            user_groups__members=user,
            user_groups__is_active=True,
            user_groups__deleted_at__isnull=True,
        ).values_list('tenant_id', flat=True)
    )
    # Managed reach.
    ids |= managed_accessible_tenant_ids(user)
    return ids


def tenant_access_report(tenant, external_only=False):
    """Return who can access ``tenant`` and how — for the per-tenant "Who Has Access" audit.

    Result is a list of dicts ``{user, sources, groups, permissions}`` sorted by username,
    where ``sources`` is a sorted list drawn from ``{'membership', 'group', 'managed'}``,
    ``groups`` is the sorted list of user-group names contributing access (empty when
    none), and ``permissions`` is the user's effective permission set in this tenant.

    With ``external_only=True`` the report is restricted to users who reach the tenant
    WITHOUT a local membership row (any ``is_active`` state): provider staff arriving via
    managed-reach grants at ``tenant.managed_by`` and members of user groups carrying
    this tenant's roles. This feeds the read-only "Access from outside this tenant"
    panel on the members list — grants shown there are managed where they live.
    """
    from organization.models import Membership, RoleAssignment
    from users.models import UserGroup

    user_data = {}

    def _get_user_entry(user):
        if user.pk not in user_data:
            user_data[user.pk] = {
                'user': user, 'sources': set(), 'groups': set(), 'permissions': set(),
            }
        return user_data[user.pk]

    # 1. Direct memberships: own-reach assignments in this tenant.
    #    (Skipped for external_only — local members are excluded below anyway.)
    if not external_only:
        direct = RoleAssignment.objects.filter(
            reach=RoleAssignment.REACH_OWN,
            membership__tenant=tenant,
            membership__is_active=True,
        ).select_related('membership__user', 'role')
        seen_membership_users = set()
        for assignment in direct:
            entry = _get_user_entry(assignment.membership.user)
            entry['sources'].add('membership')
            seen_membership_users.add(assignment.membership.user_id)
            if assignment.role.deleted_at is None:
                entry['permissions'].update(assignment.role.permissions or [])
        # Role-less members still belong to the tenant (zero perms, but visible).
        for membership in Membership.objects.filter(
            tenant=tenant, is_active=True,
        ).exclude(user_id__in=seen_membership_users).select_related('user'):
            _get_user_entry(membership.user)['sources'].add('membership')

    # 2. User groups carrying roles owned by this tenant.
    for group in UserGroup.objects.filter(
        roles__tenant=tenant, roles__deleted_at__isnull=True, is_active=True,
    ).distinct().prefetch_related('members', 'roles'):
        group_perms = set()
        for role in group.roles.all():
            if role.deleted_at is None and role.tenant_id == tenant.pk:
                group_perms.update(role.permissions or [])
        if group_perms:
            for user in group.members.all():
                entry = _get_user_entry(user)
                entry['sources'].add('group')
                entry['groups'].add(group.name)
                entry['permissions'].update(group_perms)

    # 3. Managed reach from the managing tenant.
    if tenant.managed_by_id:
        staff_assignments = RoleAssignment.objects.filter(
            reach=RoleAssignment.REACH_MANAGED,
            membership__tenant_id=tenant.managed_by_id,
            membership__is_active=True,
        ).select_related('membership__user', 'role', 'scope_group')
        for assignment in staff_assignments:
            if assignment.role.deleted_at is None and assignment.covers_tenant(tenant):
                entry = _get_user_entry(assignment.membership.user)
                entry['sources'].add('managed')
                entry['permissions'].update(assignment.role.permissions or [])

    if external_only:
        # "Without a local membership row" is literal: even a suspended
        # (is_active=False) membership means the person is managed on the members
        # list itself, not in the outside-access panel. Membership's default
        # manager is deliberately unscoped, so this works from any tenant context.
        local_user_ids = set(
            Membership.objects.filter(tenant=tenant).values_list('user_id', flat=True)
        )
        user_data = {
            pk: data for pk, data in user_data.items() if pk not in local_user_ids
        }

    report = []
    for data in user_data.values():
        report.append({
            'user': data['user'],
            'sources': sorted(data['sources']),
            'groups': sorted(data['groups']),
            'permissions': sorted(data['permissions']),
        })
    report.sort(key=lambda r: (r['user'].username or '').lower())
    return report
