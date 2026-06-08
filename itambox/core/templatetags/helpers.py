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

def _table_model(table):
    """Resolve the model backing a django_tables2 table instance."""
    meta = getattr(table, 'Meta', None)
    model = getattr(meta, 'model', None) if meta is not None else None
    if model is None:
        opts = getattr(table, '_meta', None)
        model = getattr(opts, 'model', None) if opts is not None else None
    return model


@register.simple_tag(takes_context=True)
def bulk_action_context(context, table):
    """
    Derive bulk-action wiring (URLs, permissions, selectability) for a table from
    its model. Used to render a batch-action bar for tables embedded in detail-view
    tabs, which would otherwise have no bulk infrastructure of their own.
    """
    from itambox.utils import get_model_viewname

    result = {
        'model_name_str': None,
        'bulk_delete_url': None,
        'bulk_edit_url': None,
        'can_delete': False,
        'can_change': False,
        'selectable': False,
        'return_url': '',
    }

    model = _table_model(table)
    if model is None:
        return result

    app_label = model._meta.app_label
    model_name = model._meta.model_name
    result['model_name_str'] = f'{app_label}.{model_name}'

    for action, key in (('bulk_delete', 'bulk_delete_url'), ('bulk_edit', 'bulk_edit_url')):
        try:
            result[key] = reverse(get_model_viewname(model, action))
        except NoReverseMatch:
            try:
                result[key] = reverse(action)
            except NoReverseMatch:
                result[key] = None

    # A table is selectable only if it actually renders a visible pk checkbox column.
    for column in getattr(table, 'columns', []):
        if getattr(column, 'name', None) == 'pk' and getattr(column, 'visible', False):
            result['selectable'] = True
            break

    request = context.get('request')
    if request is not None and request.user.is_authenticated:
        result['can_delete'] = request.user.has_perm(f'{app_label}.delete_{model_name}')
        result['can_change'] = request.user.has_perm(f'{app_label}.change_{model_name}')
        result['return_url'] = request.get_full_path()

    return result

# Add other helpers from NetBox as needed later.