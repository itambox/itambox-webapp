from itambox.release import VERSION


def settings_processor(request):
    """
    Expose a minimal, explicit settings dict to templates ({{ settings.VERSION }}).
    Deliberately NOT the full Django settings object — only what templates need.
    """
    return {'settings': {'VERSION': VERSION}}


def notifications_processor(request):
    """Context processor providing unread notification counts and items globally."""
    if request.user.is_authenticated:
        from django.utils.functional import SimpleLazyObject
        from core.models import Notification
        
        def get_unread_count():
            return Notification.objects.filter(user=request.user, is_read=False).count()
            
        def get_recent_unread():
            return Notification.objects.filter(user=request.user, is_read=False).order_by('-created_at')[:5]

        return {
            'unread_notifications_count': SimpleLazyObject(get_unread_count),
            'recent_unread_notifications': SimpleLazyObject(get_recent_unread)
        }
    return {
        'unread_notifications_count': 0,
        'recent_unread_notifications': []
    }


def tenant_switcher_processor(request):
    """Context processor providing structured tenants for the workspace switcher.

    Non-superuser switcher entries are grouped into two sections (RBAC stage-3
    §5): ``own_tenants_switcher`` -- "Your organization", the tenants the user
    directly belongs to (an active ``Membership`` row), ``is_provider`` ones
    pinned first -- and ``grouped_managed_tenants_switcher`` -- "Managed
    tenants", every OTHER tenant reachable via
    ``organization.access.accessible_tenant_ids`` (managed-reach grants or a
    cross-tenant UserGroup role) but with no direct membership here, still
    bucketed by TenantGroup like before.
    """
    if not request.user.is_authenticated:
        return {
            'all_tenants_switcher': [],
            'grouped_tenants_switcher': [],
            'own_tenants_switcher': [],
            'grouped_managed_tenants_switcher': [],
        }

    from django.utils.functional import SimpleLazyObject
    from organization.models import Tenant, Membership
    from collections import defaultdict

    def get_all_tenants():
        if not request.user.is_superuser:
            return []
        return Tenant._base_manager.all().order_by('name')

    def get_grouped_tenants():
        if not request.user.is_superuser:
            return []
        tenants = Tenant._base_manager.all().select_related('group').order_by('group__name', 'name')

        group_map = defaultdict(list)
        for t in tenants:
            group_map[t.group].append(t)

        sorted_groups = sorted([g for g in group_map.keys() if g is not None], key=lambda g: g.name.lower())

        grouped = []
        for g in sorted_groups:
            grouped.append({
                'group': g,
                'tenants': group_map[g]
            })
        if None in group_map:
            grouped.append({
                'group': None,
                'tenants': group_map[None]
            })
        return grouped

    def _direct_membership_tenant_ids():
        # A suspended membership can't be switched into.
        return set(
            Membership._base_manager.filter(
                user=request.user, is_active=True,
            ).values_list('tenant_id', flat=True)
        )

    def _bucket_by_group(tenants):
        """Bucket an iterable of Tenant rows into TenantGroup sections
        (alphabetical by group name, ungrouped last) -- the switcher's
        established subgrouping shape."""
        group_map = defaultdict(list)
        for t in tenants:
            group_map[t.group].append(t)

        sorted_groups = sorted([g for g in group_map if g is not None], key=lambda g: g.name.lower())

        grouped = [{'group': g, 'tenants': group_map[g]} for g in sorted_groups]
        if None in group_map:
            grouped.append({'group': None, 'tenants': group_map[None]})
        return grouped

    def get_own_tenants():
        """"Your organization": tenants with a direct (active) membership,
        is_provider ones pinned first. Flat -- not TenantGroup-bucketed; that
        subgrouping is reserved for the reach-derived "Managed tenants" section."""
        if request.user.is_superuser:
            return []
        direct_ids = _direct_membership_tenant_ids()
        if not direct_ids:
            return []
        return list(
            Tenant._base_manager.filter(pk__in=direct_ids).order_by('-is_provider', 'name')
        )

    def get_grouped_managed_tenants():
        """"Managed tenants": reach-derived access (managed-reach RoleAssignment
        grants, or a role carried by a cross-tenant UserGroup) WITHOUT a direct
        membership row here. TenantGroup subgrouping preserved within."""
        if request.user.is_superuser:
            return []
        from organization.access import accessible_tenant_ids
        all_ids = accessible_tenant_ids(request.user)
        managed_ids = all_ids - _direct_membership_tenant_ids()
        if not managed_ids:
            return []
        tenants = (
            Tenant._base_manager.filter(pk__in=managed_ids)
            .select_related('group')
            .order_by('group__name', 'name')
        )
        return _bucket_by_group(tenants)

    return {
        'all_tenants_switcher': SimpleLazyObject(get_all_tenants),
        'grouped_tenants_switcher': SimpleLazyObject(get_grouped_tenants),
        'own_tenants_switcher': SimpleLazyObject(get_own_tenants),
        'grouped_managed_tenants_switcher': SimpleLazyObject(get_grouped_managed_tenants),
    }


def base_template_processor(request):
    """
    Determine the base template to extend based on whether the request is a boosted HTMX request.
    This ensures that views do not double-render the main layout when loaded via HTMX boosted links,
    while still rendering the full layout for direct page loads.
    """
    if hasattr(request, 'base_template'):
        return {'base_template': request.base_template}

    base_template = 'layout.html'
    if getattr(request, 'htmx', False):
        target = getattr(request.htmx, 'target', '') or ''
        is_boosted_main_swap = (
            getattr(request.htmx, 'boosted', False) or
            getattr(request.htmx, 'history_restore_request', False) or
            target in ('page-content-wrapper', '#page-content-wrapper', 'page-body-main', '#page-body-main')
        )
        if is_boosted_main_swap:
            base_template = 'base_htmx.html'

    return {'base_template': base_template}
