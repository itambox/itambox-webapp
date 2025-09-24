from django import template
from django.utils.safestring import mark_safe

from extras.dashboard.widgets import get_widget

register = template.Library()


@register.simple_tag(takes_context=True)
def render_widget(context, widget_config, index=0):
    """
    Render a dashboard widget from its config dict.
    
    Usage: {% render_widget widget_config index %}
    """
    request = context.get('request')
    if not request:
        return ''

    widget_id = widget_config.get('widget')
    widget_cls = get_widget(widget_id)
    if not widget_cls:
        return f'<!-- Unknown widget: {widget_id} -->'

    try:
        instance = widget_cls(config=widget_config)
        instance.index = index
        return mark_safe(instance.render(request))
    except Exception as e:
        return f'<!-- Widget error ({widget_id}): {e} -->'


@register.simple_tag(takes_context=True)
def get_widget_footer_links(context, widget_config, index=0):
    """
    Get the footer links for a dashboard widget.
    
    Usage: {% get_widget_footer_links widget_config index %}
    """
    request = context.get('request')
    if not request:
        return []
    widget_id = widget_config.get('widget')
    widget_cls = get_widget(widget_id)
    if not widget_cls:
        return []
    try:
        instance = widget_cls(config=widget_config)
        instance.index = index
        if hasattr(instance, 'get_footer_links'):
            return instance.get_footer_links(request)
    except Exception:
        pass
    return []
