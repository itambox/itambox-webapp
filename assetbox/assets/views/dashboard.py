from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView
from assetbox.views.htmx import BaseHTMXView


class DashboardView(LoginRequiredMixin, BaseHTMXView, TemplateView):
    template_name = 'dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Dashboard'
        context['breadcrumbs'] = [(None, 'Dashboard')]

        # Resolve active dashboard ID from query params or session cache
        dashboard_id = self.request.GET.get('dashboard')
        if not dashboard_id:
            dashboard_id = self.request.session.get('active_dashboard_id')

        from extras.dashboard.utils import get_dashboard
        dashboard = get_dashboard(self.request.user, dashboard_id=dashboard_id)

        # Persist active dashboard ID in the session
        self.request.session['active_dashboard_id'] = dashboard.id

        widget_list = []
        for idx, config in enumerate(dashboard.layout):
            if not config.get('visible', True):
                continue
            widget_list.append({'index': idx, 'config': config})

        # Inject multi-dashboard properties into context
        context['active_dashboard'] = dashboard
        context['dashboard_widgets'] = widget_list
        context['dashboards_list'] = self.request.user.dashboards.all()
        context['disable_history_cache'] = True
        return context
