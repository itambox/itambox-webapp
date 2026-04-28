# This file is adapted from NetBox (https://github.com/netbox-community/netbox).
# Copyright (c) DigitalOcean, LLC.
# Licensed under the Apache License, Version 2.0.

from django import template
from django.utils import timezone
from django.utils.html import format_html
from django.urls import reverse, NoReverseMatch
from django.contrib.contenttypes.models import ContentType

register = template.Library()

# --- Filters --- #

@register.filter(name='annotated_time')
def annotated_time(value):
    """
    Display a datetime object in ISO format with a title attribute showing the relative time.
    Simplified version of NetBox's filter.
    """
    if not value:
        return '--'
    try:
        # Format for display (e.g., YYYY-MM-DD HH:MM:SS)
        display_time = timezone.localtime(value).strftime('%Y-%m-%d %H:%M:%S')
        # Title attribute with ISO format
        iso_time = value.isoformat()
        return format_html('<span title="{}">{}</span>', iso_time, display_time)
    except AttributeError:
        return str(value)

@register.filter(name='linkify')
def linkify(value):
    """
    Render a link for an object if it has a get_absolute_url() method.
    Simplified version.
    """
    if not value:
        return '--'
    try:
        url = value.get_absolute_url()
        return format_html('<a href="{}">{}</a>', url, value)
    except (AttributeError, NoReverseMatch):
        return str(value)

# --- Tags --- #

@register.simple_tag(name='get_action_color')
def get_action_color(action_value):
    """
    Return the appropriate Bootstrap background color class based on the ObjectChange action.
    Relies on the ChoiceSet having color defined (or uses defaults).
    """
    # We need access to ObjectChangeActionChoices here. Import locally.
    from core.choices import ObjectChangeActionChoices
    
    # Find the choice tuple matching the action_value
    for value, label, color in ObjectChangeActionChoices.CHOICES:
        if value == action_value:
            return color
    return 'secondary' # Default color if not found

# Add other helpers from NetBox as needed later. 