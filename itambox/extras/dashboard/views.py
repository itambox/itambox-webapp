import json
import logging
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from extras.dashboard.forms import DashboardWidgetAddForm, DashboardWidgetConfigForm
from extras.dashboard.utils import get_dashboard, get_default_dashboard
from extras.dashboard.widgets import get_widget, get_registered_widgets
from extras.models import Dashboard


class DashboardWidgetAddView(LoginRequiredMixin, View):
    """Modal: add a new widget to the dashboard."""

    def get(self, request, dashboard_id):
        form = DashboardWidgetAddForm()
        html = render_to_string('extras/dashboard/widget_add.html', {
            'form': form,
            'dashboard_id': dashboard_id,
        }, request=request)
        return HttpResponse(html)

    def post(self, request, dashboard_id):
        form = DashboardWidgetAddForm(request.POST)
        if form.is_valid():
            widget_id = form.cleaned_data['widget']
            title = form.cleaned_data['title']
            widget_cls = get_widget(widget_id)
            if widget_cls:
                dashboard = get_dashboard(request.user, dashboard_id=dashboard_id, for_update=True)
                dashboard.add_widget(widget_id, title=title or widget_cls.title)
            if request.headers.get('HX-Request'):
                response = HttpResponse()
                response['HX-Redirect'] = reverse('dashboard')
                return response
            return redirect('dashboard')
        
        # If invalid, re-render
        html = render_to_string('extras/dashboard/widget_add.html', {
            'form': form,
            'dashboard_id': dashboard_id,
        }, request=request)
        return HttpResponse(html, status=400)


class DashboardWidgetConfigView(LoginRequiredMixin, View):
    """Modal: configure a widget's title and visibility."""

    def get(self, request, dashboard_id, index):
        dashboard = get_dashboard(request.user, dashboard_id=dashboard_id)
        if not (0 <= index < len(dashboard.layout)):
            return HttpResponse('Widget not found', status=404)
        config = dashboard.layout[index]
        widget_id = config.get('widget')
        
        style_val = config.get('style', 'default')

        form = DashboardWidgetConfigForm(initial={
            'title': config.get('title', ''),
            'visible': config.get('visible', True),
            'style': style_val,
        }, widget_id=widget_id, initial_config=config, request=request)
        html = render_to_string('extras/dashboard/widget_config.html', {
            'form': form,
            'index': index,
            'config': config,
            'dashboard_id': dashboard_id,
            'widget_config_form': form.widget_config_form,
        }, request=request)
        return HttpResponse(html)

    def post(self, request, dashboard_id, index):
        dashboard = get_dashboard(request.user, dashboard_id=dashboard_id, for_update=True)
        if not (0 <= index < len(dashboard.layout)):
            return redirect('dashboard')
        config = dashboard.layout[index]
        widget_id = config.get('widget')
        form = DashboardWidgetConfigForm(request.POST, widget_id=widget_id, initial_config=config, request=request)
        if form.is_valid():
            update_kwargs = {
                'title': form.cleaned_data['title'],
                'visible': form.cleaned_data['visible'],
                'style': form.cleaned_data['style'],
            }
            widget_cfg = form.get_widget_config()
            if widget_cfg:
                existing_cfg = config.get('config', {})
                existing_cfg.update(widget_cfg)
                update_kwargs['config'] = existing_cfg
            dashboard.update_widget(index, **update_kwargs)
            if request.headers.get('HX-Request'):
                response = HttpResponse()
                response['HX-Redirect'] = reverse('dashboard')
                return response
            return redirect('dashboard')

        # If invalid, re-render the modal content with form containing errors!
        html = render_to_string('extras/dashboard/widget_config.html', {
            'form': form,
            'index': index,
            'config': config,
            'dashboard_id': dashboard_id,
            'widget_config_form': form.widget_config_form,
        }, request=request)
        return HttpResponse(html, status=400)


class DashboardWidgetDeleteView(LoginRequiredMixin, View):
    """Modal: confirm and delete a widget."""

    def get(self, request, dashboard_id, index):
        dashboard = get_dashboard(request.user, dashboard_id=dashboard_id)
        if not (0 <= index < len(dashboard.layout)):
            return HttpResponse('Widget not found', status=404)
        config = dashboard.layout[index]
        widget_cls = get_widget(config.get('widget', ''))
        html = render_to_string('extras/dashboard/widget_delete.html', {
            'index': index,
            'config': config,
            'dashboard_id': dashboard_id,
            'widget_cls': widget_cls,
        }, request=request)
        return HttpResponse(html)

    def post(self, request, dashboard_id, index):
        dashboard = get_dashboard(request.user, dashboard_id=dashboard_id, for_update=True)
        dashboard.remove_widget(index)
        if request.headers.get('HX-Request'):
            response = HttpResponse()
            response['HX-Redirect'] = reverse('dashboard')
            return response
        return redirect('dashboard')


class DashboardResetView(LoginRequiredMixin, View):
    """Reset dashboard to default layout."""

    def post(self, request, dashboard_id):
        dashboard = get_dashboard(request.user, dashboard_id=dashboard_id, for_update=True)
        dashboard.layout = get_default_dashboard()
        dashboard.save(update_fields=['layout'])
        return redirect('dashboard')


class DashboardSaveLayoutView(LoginRequiredMixin, View):
    """Save grid positions (w, h, x, y) from GridStack.js drag-and-drop/resize."""

    def post(self, request, dashboard_id):
        import json
        logger = logging.getLogger(__name__)
        dashboard = get_dashboard(request.user, dashboard_id=dashboard_id, for_update=True)
        try:
            data = json.loads(request.body)
            widgets = data.get('widgets', [])
            if widgets:
                for w in widgets:
                    index = w.get('index')
                    if index is not None and 0 <= index < len(dashboard.layout):
                        dashboard.layout[index]['w'] = w.get('w', 4)
                        dashboard.layout[index]['h'] = w.get('h', 2)
                        dashboard.layout[index]['x'] = w.get('x')
                        dashboard.layout[index]['y'] = w.get('y')
                dashboard.save(update_fields=['layout'])
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to parse dashboard layout JSON: %s", e)
        return HttpResponse('ok')


from organization.models import Tenant

class DashboardManageModalView(LoginRequiredMixin, View):
    """Modal view to manage the user's dashboards."""

    def get(self, request):
        dashboards = request.user.dashboards.all()
        tenants = Tenant.objects.all().order_by('name')
        html = render_to_string('extras/dashboard/manage_dashboards.html', {
            'dashboards': dashboards,
            'tenants': tenants,
        }, request=request)
        return HttpResponse(html)


class DashboardCreateView(LoginRequiredMixin, View):
    """Create a new dashboard for the authenticated user."""

    def post(self, request):
        name = request.POST.get('name', '').strip()
        tenant_id = request.POST.get('tenant', '').strip()

        if not name:
            name = "New Dashboard"

        if not tenant_id:
            from django.contrib import messages
            if request.headers.get('HX-Request'):
                return HttpResponse('<div class="alert alert-danger mb-0">Tenant is required.</div>', status=400)
            messages.error(request, "Tenant is required.")
            return redirect('dashboard')

        tenant = Tenant.objects.filter(id=tenant_id).first()
        if not tenant:
            from django.contrib import messages
            if request.headers.get('HX-Request'):
                return HttpResponse('<div class="alert alert-danger mb-0">Selected tenant does not exist.</div>', status=400)
            messages.error(request, "Selected tenant does not exist.")
            return redirect('dashboard')

        # If this is the user's first dashboard, make it the default
        is_default = not request.user.dashboards.exists()

        dashboard = Dashboard.objects.create(
            user=request.user,
            name=name,
            tenant=tenant,
            is_default=is_default,
            layout=get_default_dashboard()
        )

        # Set the active dashboard to the newly created one
        request.session['active_dashboard_id'] = dashboard.id

        if request.headers.get('HX-Request'):
            response = HttpResponse()
            response['HX-Redirect'] = f"/?dashboard={dashboard.id}"
            return response
        return redirect('dashboard')


class DashboardDeleteView(LoginRequiredMixin, View):
    """Delete a specific dashboard."""

    def post(self, request, pk):
        dashboard = get_object_or_404(Dashboard, pk=pk, user=request.user)

        # Prevent deleting if it's the last dashboard
        if request.user.dashboards.count() <= 1:
            if request.headers.get('HX-Request'):
                return HttpResponse('<div class="alert alert-danger mb-0">You must keep at least one dashboard.</div>', status=400)
            return redirect('dashboard')

        was_default = dashboard.is_default
        dashboard.delete()

        # If we deleted the default dashboard, set the first remaining one as default
        if was_default:
            first_db = request.user.dashboards.first()
            if first_db:
                first_db.is_default = True
                first_db.save(update_fields=['is_default'])

        # Reset session pointer if we deleted the active dashboard
        if request.session.get('active_dashboard_id') == pk:
            first_db = request.user.dashboards.first()
            if first_db:
                request.session['active_dashboard_id'] = first_db.id

        if request.headers.get('HX-Request'):
            response = HttpResponse()
            response['HX-Redirect'] = reverse('dashboard')
            return response
        return redirect('dashboard')


class DashboardRenameView(LoginRequiredMixin, View):
    """Rename a specific dashboard."""

    def post(self, request, pk):
        dashboard = get_object_or_404(Dashboard, pk=pk, user=request.user)
        name = request.POST.get('name', '').strip()
        if name:
            dashboard.name = name
            dashboard.save(update_fields=['name'])

        if request.headers.get('HX-Request'):
            response = HttpResponse()
            response['HX-Redirect'] = reverse('dashboard')
            return response
        return redirect('dashboard')


class DashboardSetDefaultView(LoginRequiredMixin, View):
    """Set a specific dashboard as default."""

    def post(self, request, pk):
        dashboard = get_object_or_404(Dashboard, pk=pk, user=request.user)

        # Clear default flag on other dashboards
        request.user.dashboards.all().update(is_default=False)

        dashboard.is_default = True
        dashboard.save(update_fields=['is_default'])

        if request.headers.get('HX-Request'):
            response = HttpResponse()
            response['HX-Redirect'] = reverse('dashboard')
            return response
        return redirect('dashboard')
