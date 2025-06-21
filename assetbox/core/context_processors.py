from django.urls import reverse
from django.utils.translation import gettext_lazy as _

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
    # current_label = # Need a way to get current page title? View context again.
    # if request.path != items[-1][1]:
    #     items.append(("Current Page", request.path))

    # Remove items with None URL except potentially the last one
    final_items = []
    for i, (url, label) in enumerate(items):
        if url is not None or i == len(items) - 1:
            final_items.append((url, label))

    return {'breadcrumbs': final_items}


def notifications_processor(request):
    """Context processor providing unread notification counts and items globally."""
    if request.user.is_authenticated:
        from core.models import Notification
        unread_qs = Notification.objects.filter(user=request.user, is_read=False)
        unread_count = unread_qs.count()
        recent_unread = unread_qs.order_by('-created_at')[:5]
        return {
            'unread_notifications_count': unread_count,
            'recent_unread_notifications': recent_unread
        }
    return {
        'unread_notifications_count': 0,
        'recent_unread_notifications': []
    }