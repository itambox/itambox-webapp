from .constants import DEFAULT_PAGINATE_COUNT, PAGINATE_COUNT_CHOICES

def get_model_viewname(model, action):
    """
    Return the conventional view name for the given model and action.
    Example: (Asset, 'list') -> 'assets:asset_list'
    """
    app_label = model._meta.app_label
    model_name = model._meta.model_name
    return f"{app_label}:{model_name}_{action}"

def get_paginate_count(request):
    """
    Determine the number of objects to display per page.
    Checks request query parameters first, then user preferences (TODO), then default.
    """
    # Check for per_page query parameter
    try:
        per_page = int(request.GET.get('per_page', 0))
        # Validate against choices
        if per_page in dict(PAGINATE_COUNT_CHOICES):
            print(f"[get_paginate_count] Using per_page from query param: {per_page}") # DEBUG
            return per_page
    except ValueError:
        pass

    # TODO: Check user preferences
    # if request.user.is_authenticated:
    #    user_pref = request.user.preferences.data.get('pagination', {}).get('per_page')
    #    if user_pref in dict(PAGINATE_COUNT_CHOICES):
    #        return user_pref

    # Fallback to default
    print(f"[get_paginate_count] Using default per_page: {DEFAULT_PAGINATE_COUNT}") # DEBUG
    return DEFAULT_PAGINATE_COUNT 