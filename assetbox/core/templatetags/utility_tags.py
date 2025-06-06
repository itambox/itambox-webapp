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