from django import template
from django.template.loader import get_template
from django.template import TemplateDoesNotExist

register = template.Library()


@register.inclusion_tag('generic/includes/panel_wrapper.html', takes_context=True)
def render_panel(context, panel):
    """
    Render a panel from a detail view layout.
    
    Looks for a template include at:
      {app_label}/includes/detail/{model_name}_{panel.name}.html
    
    Falls back to rendering empty content if no template found.
    Subclass templates define these includes to provide panel content.
    """
    model = context.get('model')
    panel_template = None
    if model:
        app_label = model._meta.app_label
        model_name = model._meta.model_name
        candidates = [
            f'{app_label}/includes/detail/{model_name}_{panel.name}.html',
            f'{app_label}/includes/{panel.name}_panel.html',
        ]
        for candidate in candidates:
            try:
                get_template(candidate)
                panel_template = candidate
                break
            except TemplateDoesNotExist:
                continue

    return {
        'panel': panel,
        'panel_template': panel_template,
    }
