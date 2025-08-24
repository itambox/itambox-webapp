from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView
from assetbox.views.htmx import BaseHTMXView


class DashboardView(LoginRequiredMixin, BaseHTMXView, TemplateView):
    template_name = 'dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Dashboard'
        context['breadcrumbs'] = [(None, 'Dashboard')]

        from extras.dashboard.utils import get_dashboard
        dashboard = get_dashboard(self.request.user)

        widget_list = []
        for idx, config in enumerate(dashboard.layout):
            if not config.get('visible', True):
                continue
            widget_list.append({'index': idx, 'config': config})

        context['dashboard_widgets'] = widget_list
        return context
