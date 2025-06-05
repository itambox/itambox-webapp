# assetbox/templatetags/utility_tags.py
from django import template
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
