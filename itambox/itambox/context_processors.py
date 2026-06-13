from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from itambox.release import VERSION


def settings_processor(request):
    """
    Expose a minimal, explicit settings dict to templates ({{ settings.VERSION }}).
    Deliberately NOT the full Django settings object — only what templates need.
    """
    return {'settings': {'VERSION': VERSION}}


def breadcrumbs(request):
    """
    Generate breadcrumbs based on the current request path.
    This is a basic implementation and might need refinement based on specific views.
    """
    # Get view name and args/kwargs from the request resolver
    resolver_match = request.resolver_match
    if not resolver_match:
        return {'breadcrumbs': []}

    view_name = resolver_match.view_name
    view_args = resolver_match.args
    view_kwargs = resolver_match.kwargs
    app_name = resolver_match.app_name

    # Start with Home/Dashboard
    items = [
        (reverse('dashboard'), _('Dashboard'))
    ]

    # Basic app/model detection (can be expanded)
    if app_name and view_name:
        app_label = app_name.title()
        model_name = None
        action = None

        # Try to infer model/action from view name (e.g., assets:asset_list)
        parts = view_name.split(':')
        if len(parts) == 2:
            view_parts = parts[1].split('_')
            if len(view_parts) > 1:
                model_name = view_parts[0].title()
                action = view_parts[-1]

                # Add App breadcrumb (linking to first model? or no link?)
                # For simplicity, let's not link the app label for now.
                items.append((None, app_label))

                # Add Model List breadcrumb
                try:
                    list_url_name = f"{app_name}:{view_parts[0]}_list"
                    list_url = reverse(list_url_name)
                    items.append((list_url, f"{model_name}s")) # Pluralize naively
                except:
                    # Fallback if list view doesn't exist or naming fails
                    items.append((None, f"{model_name}s"))

                # Add Action breadcrumb (Create/Edit/Delete)
                if action in ('create', 'update', 'delete'):
                    items.append((request.path, action.title()))
                # Handle detail view? Need object context from view.
                # elif action == 'detail':
                #    pass # Requires getting object from context

    # Add current page if not already last item
    # last_label = items[-1][0]

    # Remove items with None URL except potentially the last one
    final_items = []
    for i, (url, label) in enumerate(items):
        if url is not None or i == len(items) - 1:
            final_items.append((url, label))

    return {'breadcrumbs': final_items}


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
        memberships = TenantMembership.objects.filter(user=request.user).select_related('tenant', 'tenant__group').order_by('tenant__group__name', 'tenant__name')
        
        group_map = defaultdict(list)
        for m in memberships:
            group_map[m.tenant.group].append(m)
            
        sorted_groups = sorted([g for g in group_map.keys() if g is not None], key=lambda g: g.name.lower())
        
        grouped = []
        for g in sorted_groups:
            grouped.append({
                'group': g,
                'memberships': group_map[g]
            })
        if None in group_map:
            grouped.append({
                'group': None,
                'memberships': group_map[None]
            })
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
