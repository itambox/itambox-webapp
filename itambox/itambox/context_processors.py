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
    """Context processor providing structured tenants and memberships grouped by TenantGroup."""
    if not request.user.is_authenticated:
        return {
            'all_tenants_switcher': [],
            'grouped_tenants_switcher': [],
            'grouped_memberships_switcher': []
        }

    from django.utils.functional import SimpleLazyObject
    from organization.models import Tenant, TenantMembership
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

    def get_grouped_memberships():
        if request.user.is_superuser:
            return []
        # Tenants the user may switch into: active direct memberships UNION tenants
        # granted via active cross-tenant user groups. (A suspended membership cannot
        # be switched into and is excluded by accessible_tenant_ids.)
        from organization.access import accessible_tenant_ids
        ids = accessible_tenant_ids(request.user)
        if not ids:
            return []
        tenants = (
            Tenant._base_manager.filter(pk__in=ids)
            .select_related('group')
            .order_by('group__name', 'name')
        )
        group_map = defaultdict(list)
        for t in tenants:
            group_map[t.group].append(t)

        sorted_groups = sorted([g for g in group_map.keys() if g is not None], key=lambda g: g.name.lower())

        grouped = []
        for g in sorted_groups:
            grouped.append({'group': g, 'tenants': group_map[g]})
        if None in group_map:
            grouped.append({'group': None, 'tenants': group_map[None]})
        return grouped

    return {
        'all_tenants_switcher': SimpleLazyObject(get_all_tenants),
        'grouped_tenants_switcher': SimpleLazyObject(get_grouped_tenants),
        'grouped_memberships_switcher': SimpleLazyObject(get_grouped_memberships)
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
