# assetbox/templatetags/utility_tags.py
from django import template
from django.contrib.messages import constants as messages
from urllib.parse import urlencode

register = template.Library()

@register.filter
def lookup(dictionary, key):
    """Enable dictionary lookup by variable key in templates."""
    return dictionary.get(key)

@register.simple_tag(takes_context=True)
def get_active_filters(context):
    """
    Checks if there are any active filters in the request GET parameters,
    excluding pagination and search ('page', 'per_page', 'q').
    """
    request = context.get('request')
    if not request:
        return False

    ignored_params = ['page', 'per_page', 'q']
    for key in request.GET:
        if key not in ignored_params:
            return True # Found an active filter
    return False

@register.filter
def status_from_tag(tag):
    """
    Map Django message tags (strings) to Bootstrap alert classes.
    """
    if tag == 'debug':
        return 'secondary'
    elif tag == 'info':
        return 'info'
    elif tag == 'success':
        return 'success'
    elif tag == 'warning':
        return 'warning'
    elif tag == 'error': # Django's 'error' level tag maps to Bootstrap 'danger'
        return 'danger'
    return 'info' # Default

@register.filter
def icon_from_status(status):
    """
    Map Bootstrap alert class/status to a Tabler icon name.
    (Using Tabler icons which are often based on MDI names)
    """
    if status == 'secondary': # debug
        return 'bug' # Tabler 'bug' icon
    elif status == 'info':
        return 'info-circle' # Tabler 'info-circle' icon
    elif status == 'success':
        return 'circle-check' # Tabler 'circle-check' icon
    elif status == 'warning':
        return 'alert-triangle' # Tabler 'alert-triangle' icon
    elif status == 'danger': # error
        return 'alert-circle' # Tabler 'alert-circle' icon
    return 'info-circle' # Default

@register.simple_tag()
def update_querystring(request, **kwargs):
    """
    Renders the current page's query string with updated parameter values.
    Matches NetBox's implementation signature.
    Example: {% update_querystring request page=paginator.next_page_number %}
    """
    if not request:
        return ''
    
    print(f"[update_querystring] Initial request.GET: {request.GET}") # DEBUG
    print(f"[update_querystring] Initial kwargs: {kwargs}") # DEBUG
    
    query_params = request.GET.copy() # Get a mutable copy
    
    # Update parameters from kwargs
    for key, value in kwargs.items():
        # Ensure value is string, handle None to remove parameter
        if value is not None:
            query_params[key] = str(value)
        elif key in query_params: # Remove key if value is None
            del query_params[key]

    # Explicitly remove any empty string values or keys
    keys_to_delete = [k for k, v in query_params.items() if v == '']
    for key in keys_to_delete:
        del query_params[key]
        
    print(f"[update_querystring] Query params before page=1 check: {query_params}") # DEBUG
    page_value = query_params.get('page')
    print(f"[update_querystring] Value of 'page' before check: {page_value} (type: {type(page_value)})", flush=True) # DEBUG

    # Don't include page=1 in the query string (it's the default)
    if 'page' in query_params and query_params['page'] == '1':
        print("[update_querystring] Condition met: Removing page=1", flush=True) # DEBUG
        del query_params['page']
    else:
        print("[update_querystring] Condition NOT met for removing page=1", flush=True) # DEBUG

    print(f"[update_querystring] Final query_params before encode: {query_params}") # DEBUG

    # --- Refined Check --- 
    # If the dictionary is empty after modifications, return empty string
    if not query_params:
        print("[update_querystring] Returning empty string (query_params dict is empty)") # DEBUG
        return ''

    # Encode the parameters - only add '?' if there are non-empty params
    encoded_params = query_params.urlencode(safe='/')
    print(f"[update_querystring] Encoded params: '{encoded_params}'") # DEBUG
    
    # Fallback check, though the dict check should cover it
    if encoded_params and encoded_params.strip():
        result = '?' + encoded_params
        print(f"[update_querystring] Returning: '{result}'") # DEBUG
        return result
    else:
        # This case should ideally not be reached if the dict check works
        print("[update_querystring] Returning empty string (encoded_params was empty/whitespace)") # DEBUG 
        return ''