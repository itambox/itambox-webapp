"""Cross-tenant access helpers.

A (non-superuser) user's accessible tenants are the union of:

  1. their active direct ``TenantMembership`` rows,
  2. the tenants of every ``TenantRole`` granted to them via an active, non-deleted
     ``UserGroup`` they belong to (groups are global and may grant roles across many
     tenants — the MSP "team" model), and
  3. tenants reachable through an active ``ProviderMembership`` (MSP staff), per the
     membership's ``tenant_scope`` (explicit assignment / tenant group / all).

This is the single source of truth for "which tenants may this user enter", used by
``TenantMiddleware`` (tenant activation) and the tenant switcher. Permission *content*
within a tenant is resolved separately by ``TenantMembershipBackend._effective_perms``.
"""


def get_descendant_tenant_group_ids(group_id):
    """Return ``group_id`` plus the ids of all its descendant TenantGroups (inclusive).

    Uses ``TenantGroup._base_manager`` (unscoped) so the true tree is walked regardless
    of the active tenant-scoping context. Iterative, cycle-safe.
    """
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
    """Tenant ids reachable via the user's active ProviderMemberships (step 3 above)."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return set()
    # inline import: avoid AppRegistryNotReady / a core<->organization cycle at load
    from organization.models import Tenant
    from users.models import ProviderMembership

    ids = set()
    memberships = ProviderMembership.objects.filter(
        user=user, is_active=True,
    ).select_related('provider', 'scope_group')
    for pm in memberships:
        if pm.tenant_scope == ProviderMembership.SCOPE_ALL:
            ids |= set(
                Tenant._base_manager.filter(provider_id=pm.provider_id)
                .values_list('pk', flat=True)
            )
        elif pm.tenant_scope == ProviderMembership.SCOPE_TENANT_GROUP:
            if pm.scope_group_id:
                group_ids = get_descendant_tenant_group_ids(pm.scope_group_id)
                ids |= set(
                    Tenant._base_manager.filter(
                        provider_id=pm.provider_id, group_id__in=group_ids,
                    ).values_list('pk', flat=True)
                )
        else:  # SCOPE_EXPLICIT
            ids |= set(
                pm.assigned_tenants.filter(provider_id=pm.provider_id)
                .values_list('pk', flat=True)
            )
    return ids


def accessible_tenant_ids(user):
    """Return the set of tenant IDs ``user`` may access.

    Superusers are global and are handled by the caller (this returns the empty set
    for an unauthenticated/None user). Result is a set of ``int`` tenant PKs.
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return set()
    # inline imports: avoid AppRegistryNotReady / a core<->organization cycle at load
    from organization.models import TenantMembership, TenantRole

    ids = set(
        TenantMembership.objects.filter(user=user, is_active=True)
        .values_list('tenant_id', flat=True)
    )
    # _base_manager: unscoped (no current-tenant filtering); the role's own tenant is
    # what matters, and group membership grants regardless of any direct membership.
    ids |= set(
        TenantRole._base_manager.filter(
            deleted_at__isnull=True,
            user_groups__members=user,
            user_groups__is_active=True,
            user_groups__deleted_at__isnull=True,
        ).values_list('tenant_id', flat=True)
    )
    # Provider grants (MSP staff): tenants within each active ProviderMembership's scope.
    ids |= provider_accessible_tenant_ids(user)
    return ids


def tenant_access_report(tenant):
    """Return who can access ``tenant`` and how — for the per-tenant "Who Has Access" audit.

    Result is a list of dicts ``{user, sources, permissions}`` sorted by username, where
    ``sources`` is a sorted list drawn from {'membership', 'group', 'provider'} and
    ``permissions`` is the user's effective permission set in this tenant.
    """
    # inline imports: avoid AppRegistryNotReady / cycles at module load
    from organization.models import TenantMembership, TenantRole
    from users.models import UserGroup, ProviderMembership
    from core.auth import TenantMembershipBackend

    sources = {}  # user_id -> (user, set(sources))

    def _add(user, source):
        entry = sources.get(user.pk)
        if entry is None:
            sources[user.pk] = (user, {source})
        else:
            entry[1].add(source)

    for m in TenantMembership.objects.filter(tenant=tenant, is_active=True).select_related('user'):
        _add(m.user, 'membership')

    groups = UserGroup.objects.filter(
        roles__tenant=tenant, is_active=True,
    ).distinct().prefetch_related('members')
    for g in groups:
        for user in g.members.all():
            _add(user, 'group')

    if getattr(tenant, 'provider_id', None):
        backend = TenantMembershipBackend()
        pms = ProviderMembership.objects.filter(
            provider_id=tenant.provider_id, is_active=True,
        ).select_related('user', 'provider_role__tenant_role_template')
        for pm in pms:
            if pm.provider_role and pm.provider_role.tenant_role_template and backend._tenant_in_scope(pm, tenant):
                _add(pm.user, 'provider')

    backend = TenantMembershipBackend()
    report = []
    for user, srcs in sources.values():
        # Fresh effective-perm resolution per user in this tenant.
        for attr in (f'_effective_perms_{tenant.pk}', f'_tenant_membership_{tenant.pk}'):
            if hasattr(user, attr):
                delattr(user, attr)
        perms = sorted(backend._effective_perms(user, tenant))
        report.append({'user': user, 'sources': sorted(srcs), 'permissions': perms})
    report.sort(key=lambda r: (r['user'].username or '').lower())
    return report
