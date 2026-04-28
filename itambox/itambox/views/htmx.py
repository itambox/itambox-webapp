from django.core.exceptions import ImproperlyConfigured
from django.shortcuts import render
from django.urls import reverse


class BaseHTMXView:
    page_body_partial_name = "htmx/page_body_content_wrapper.html"
    content_partial_name = None

    def get_template_names(self):
        if not hasattr(self, 'template_name') or not self.template_name:
            if hasattr(super(), 'get_template_names'):
                return super().get_template_names()
            raise ImproperlyConfigured(
                f"{self.__class__.__name__} needs a template_name attribute defined."
            )
        return [self.template_name]

    def render_to_response(self, context, **response_kwargs):
        request = self.request

        if getattr(request, 'htmx', False):
            context['request'] = request

            target = getattr(request.htmx, 'target', '') or ''
            is_boosted_main_swap = getattr(request.htmx, 'boosted', False) or \
                                   getattr(request.htmx, 'history_restore_request', False) or \
                                   target in ('page-content-wrapper', '#page-content-wrapper', 'page-body-main', '#page-body-main')

            if is_boosted_main_swap:
                context['base_template'] = 'base_htmx.html'
                context.setdefault('title', 'ITAMbox')
                context.setdefault('breadcrumbs', [(reverse('dashboard'), 'Dashboard'), (None, context['title'])])
                context.setdefault('page_actions', None)
            elif self.content_partial_name:
                return render(request, self.content_partial_name, context)

        if hasattr(super(), 'render_to_response'):
            return super().render_to_response(context, **response_kwargs)
        else:
            if hasattr(self, 'response_class') and hasattr(self, 'get_template_names'):
                 return self.response_class(
                    request=request,
                    template=self.get_template_names(),
                    context=context,
                    using=self.template_engine,
                    **response_kwargs
                )
            else:
                raise ImproperlyConfigured(f"{self.__class__.__name__} or its superclasses must provide a render_to_response method or be mixed with TemplateResponseMixin.")
