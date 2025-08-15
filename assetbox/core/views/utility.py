import logging
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import View
from django.views.generic.base import TemplateResponseMixin
from django.urls import reverse
from django.conf import settings
from django.utils.module_loading import import_string
from django.http import JsonResponse

from core.forms import SearchForm
from core.utils import get_table_for_model
from .generic import BaseHTMXView

logger = logging.getLogger(__name__)


class SearchView(LoginRequiredMixin, BaseHTMXView, TemplateResponseMixin, View):
    template_name = 'core/search.html'

    def get(self, request):
        query = request.GET.get('q', '').strip()
        obj_types = request.GET.getlist('obj_type') 
        lookup = request.GET.get('lookup', 'icontains')

        allowed_lookups = {'icontains', 'iexact', 'istartswith', 'iendswith', 'iregex'}
        if lookup not in allowed_lookups:
            lookup = 'icontains'

        form = SearchForm(request.GET)
        results_data = {}
        results_count = 0

        if query:
            search_backend_cls = import_string(settings.SEARCH_BACKEND)
            search_backend = search_backend_cls()
            results_data = search_backend.search(query, user=request.user, obj_types=obj_types, lookup=lookup) 

            for model, data in results_data.items():
                results_count += data['count']
                table_class = get_table_for_model(model)
                if table_class:
                    data['table'] = table_class(list(data['queryset'][:10]), request=request) 
                    try:
                        from django.urls import reverse
                        from core.utils import get_model_viewname
                        data['list_url'] = reverse(get_model_viewname(model, 'list'))
                    except Exception:
                        data['list_url'] = f'/{model._meta.app_label}/{model._meta.model_name}s/'
                else:
                    data['table'] = None

        context = {
            'form': form,
            'query': query,
            'obj_types': obj_types,
            'lookup': lookup,
            'results': results_data,
            'results_count': results_count,
            'title': 'Search Results',
            'breadcrumbs': [
                 (reverse('dashboard'), 'Dashboard'),
                 (None, 'Search')
            ]
        }
        context['content_template_name'] = self.template_name
        return self.render_to_response(context)


def health(request):
    """Health check endpoint returning 200 OK."""
    return JsonResponse({'status': 'ok'})
