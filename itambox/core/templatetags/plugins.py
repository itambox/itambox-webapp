from django import template
from django.utils.safestring import mark_safe
from itambox.registry import registry

register = template.Library()

@register.simple_tag(takes_context=True)
def plugin_template_content(context, model, position, object):
    """
    Renders template content registered by plugins for the given model and position.
    """
    if model is None:
        return ''

    # Resolve the model name string (e.g., 'assets.asset')
    if isinstance(model, str):
        model_name = model.lower()
    elif hasattr(model, '_meta'):
        model_name = model._meta.label_lower
    else:
        model_name = str(model).lower()

    content_classes = registry.get_plugin_template_contents(model_name)
    if not content_classes:
        return ''

    # Build render context for plugin template classes
    render_context = {
        'object': object,
        'request': context.get('request'),
    }
    if 'csrf_token' in context:
        render_context['csrf_token'] = context['csrf_token']

    rendered_contents = []

    for content_class in content_classes:
        try:
            instance = content_class(render_context)
            if hasattr(instance, position):
                method = getattr(instance, position)
                content = method()
                rendered_contents.append(str(content))
        except Exception as e:
            # Catch exceptions raised by plugin templates safely as an HTML comment
            comment_log = f"<!-- Error rendering plugin template content class '{content_class.__name__}' for position '{position}': {e} -->"
            rendered_contents.append(comment_log)

    return mark_safe('\n'.join(rendered_contents))
