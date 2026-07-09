"""Cross-tenant access helpers (unified RBAC).

A (non-superuser) user's accessible tenants are the union of:

  1. their active tenant ``Membership`` rows (tenant members),
  2. tenants of every ``Role`` granted to them via an active, non-deleted ``UserGroup``
     (groups are global and may grant roles across many tenants — the MSP "team" model), and
  3. tenants reachable through an active provider ``Membership`` (provider staff)
     per its ``tenant_scope`` (explicit / tenant_group / all).

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


def provider_accessible_tenant_ids(user):
    """Tenant ids reachable via the user's active provider Memberships (step 3 above).

    The per-scope tenant-id resolution lives on the model as
    ``Membership.scoped_tenant_ids`` (the canonical helper); this function just unions it
    across the user's active provider memberships. One membership → one set of scope
    queries, so this scales with the number of provider memberships a single user holds
    (small in practice), not with the tenant count.
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return set()
    from organization.models import Membership

    ids = set()
    staff_memberships = Membership.objects.filter(
        user=user, is_active=True, provider__isnull=False,
    ).select_related('provider', 'scope_group')
    for pm in staff_memberships:
        ids |= pm.scoped_tenant_ids()
    return ids


def accessible_tenant_ids(user):
    """Return the set of tenant IDs ``user`` may access."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return set()
    from organization.models import Membership, Role

    ids = set(
        Membership.objects.filter(
            user=user, is_active=True, tenant__isnull=False,
        ).values_list('tenant_id', flat=True)
    )
    # Tenant-scoped Roles attached via UserGroups (cross-tenant access).
    ids |= set(
        Role._base_manager.filter(
            scope=Role.SCOPE_TENANT, deleted_at__isnull=True,
            user_groups__members=user,
            user_groups__is_active=True,
            user_groups__deleted_at__isnull=True,
        ).values_list('tenant_id', flat=True)
    )
    # Provider staff (MSP) scopes.
    ids |= provider_accessible_tenant_ids(user)
    return ids


def accessible_provider_ids(user):
    """Return the set of provider IDs ``user`` may operate against."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return set()
    from organization.models import Membership
    return set(
        Membership.objects.filter(
            user=user, is_active=True, provider__isnull=False,
        ).values_list('provider_id', flat=True)
    )


def tenant_access_report(tenant):
    """Return who can access ``tenant`` and how — for the per-tenant "Who Has Access" audit.

    Result is a list of dicts ``{user, sources, permissions, kinds}`` sorted by
    username, where ``sources`` is a sorted list drawn from
    ``{'membership', 'group', 'provider'}``, ``permissions`` is the user's effective
    permission set in this tenant, and ``kinds`` is the set of membership kinds
    (``'member'`` / ``'staff'``) that apply (so a user with both a direct member row AND
    provider-staff coverage shows both).
    """
    from organization.models import Membership, Role
    from users.models import UserGroup

    # 1. Fetch all direct memberships for this tenant
    direct_memberships = Membership.objects.filter(
        tenant=tenant, is_active=True
    ).select_related('user').prefetch_related('roles')

    # 2. Fetch all user groups with roles in this tenant
    user_groups = UserGroup.objects.filter(
        roles__scope=Role.SCOPE_TENANT, roles__tenant=tenant, is_active=True
    ).prefetch_related('members', 'roles')

    # 3. Fetch provider staff if there is a provider
    staff_memberships = []
    if getattr(tenant, 'provider_id', None):
        staff_memberships = Membership.objects.filter(
            provider_id=tenant.provider_id, is_active=True,
        ).select_related('user', 'scope_group').prefetch_related('roles', 'assigned_tenants')

    user_data = {}

    def _get_user_entry(user):
        if user.pk not in user_data:
            user_data[user.pk] = {
                'user': user,
                'sources': set(),
                'kinds': set(),
                'permissions': set()
            }
        return user_data[user.pk]

    # Process direct memberships
    for m in direct_memberships:
        entry = _get_user_entry(m.user)
        entry['sources'].add('membership')
        entry['kinds'].add(m.kind)
        entry['permissions'].update(m.direct_permissions or [])
        for role in m.roles.all():
            if role.deleted_at is None and role.scope == Role.SCOPE_TENANT:
                entry['permissions'].update(role.permissions or [])

    # Process user groups
    for g in user_groups:
        group_perms = set()
        for role in g.roles.all():
            if role.deleted_at is None and role.scope == Role.SCOPE_TENANT and role.tenant_id == tenant.pk:
                group_perms.update(role.permissions or [])
        if group_perms:
            for user in g.members.all():
                entry = _get_user_entry(user)
                entry['sources'].add('group')
                entry['permissions'].update(group_perms)

    # Process provider staff
    for pm in staff_memberships:
        if pm.covers_tenant(tenant):
            entry = _get_user_entry(pm.user)
            entry['sources'].add('provider')
            entry['kinds'].add(Membership.KIND_STAFF)
            # Only provider-scoped roles project into tenant context, with provider
            # capabilities (organization.manage_*) stripped via the canonical helper
            # (Membership.project_permissions_for_tenant). A staff membership's own
            # direct_permissions are provider-context only and do NOT project here.
            for role in pm.roles.all():
                if role.deleted_at is None and role.scope == Role.SCOPE_PROVIDER:
                    entry['permissions'].update(
                        Membership.project_permissions_for_tenant(role.permissions)
                    )

    # Format the report
    report = []
    for data in user_data.values():
        report.append({
            'user': data['user'],
            'sources': sorted(data['sources']),
            'kinds': sorted(data['kinds']),
            'permissions': sorted(data['permissions']),
        })

    report.sort(key=lambda r: (r['user'].username or '').lower())
    return report
