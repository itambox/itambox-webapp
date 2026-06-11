import logging
from django.shortcuts import get_object_or_404, redirect
from django.http import HttpResponse
from django.views.generic import View
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.utils.decorators import method_decorator
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.template import Template, Context
from django.utils import timezone
from django.utils.translation import gettext as _
from django_q.tasks import async_task

from extras.models import ReportTemplate, ScheduledReport
from core.tables import ReportTemplateTable, ScheduledReportTable
from core.forms import ReportTemplateForm, ScheduledReportForm
from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView
)

logger = logging.getLogger(__name__)


@method_decorator(login_required, name='dispatch')
class ReportTemplateListView(ObjectListView):
    queryset = ReportTemplate.objects.all()
    table = ReportTemplateTable
    template_name = 'core/reports/report_template_list.html'
    action_buttons = ('add',)

    def get_breadcrumbs(self):
        return [
            (reverse('dashboard'), 'Dashboard'),
            (None, 'Report Templates')
        ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Report Templates'
        context['is_beta_module'] = True
        return context


@method_decorator(login_required, name='dispatch')
class ReportTemplateDetailView(ObjectDetailView):
    queryset = ReportTemplate.objects.all()
    template_name = 'core/reports/report_template_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obj = self.get_object()
        context['title'] = f"Report Template: {obj.name}"
        context['schedules'] = obj.schedules.all()
        return context


@method_decorator(login_required, name='dispatch')
class ReportTemplateCreateView(ObjectEditView):
    queryset = ReportTemplate.objects.all()
    model_form = ReportTemplateForm
    template_name = 'core/reports/report_template_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Create Report Template'
        return context


@method_decorator(login_required, name='dispatch')
class ReportTemplateUpdateView(ObjectEditView):
    queryset = ReportTemplate.objects.all()
    model_form = ReportTemplateForm
    template_name = 'core/reports/report_template_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f"Edit Report Template: {self.object.name}"
        return context


@method_decorator(login_required, name='dispatch')
class ReportTemplateDeleteView(ObjectDeleteView):
    queryset = ReportTemplate.objects.all()
    template_name = 'core/reports/report_template_confirm_delete.html'


@method_decorator(login_required, name='dispatch')
class ScheduledReportListView(ObjectListView):
    queryset = ScheduledReport.objects.select_related('report')
    table = ScheduledReportTable
    template_name = 'core/reports/report_list.html'
    action_buttons = ('add',)

    def get_breadcrumbs(self):
        return [
            (reverse('dashboard'), 'Dashboard'),
            (None, 'Scheduled Reports')
        ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Scheduled Reports'
        context['templates'] = ReportTemplate.objects.all()
        context['is_beta_module'] = True
        return context


@method_decorator(login_required, name='dispatch')
class ScheduledReportCreateView(ObjectEditView):
    queryset = ScheduledReport.objects.all()
    model_form = ScheduledReportForm
    template_name = 'core/reports/report_schedule_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Schedule a Report'
        return context

def handle_report_scheduling(sched_report):
    from django_q.models import Schedule
    from django.utils import timezone
    import datetime

    if sched_report.is_active:
        # Map frequency choice to django-q Schedule type
        freq_mapping = {
            'once': Schedule.ONCE,
            'hourly': Schedule.HOURLY,
            'daily': Schedule.DAILY,
            'weekly': Schedule.WEEKLY,
            'biweekly': 'BW',
            'monthly': Schedule.MONTHLY,
            'quarterly': 'Q',
            'yearly': 'Y',
            'cron': Schedule.CRON,
        }
        q_freq = freq_mapping.get(sched_report.frequency, Schedule.WEEKLY)
        
        defaults = {
            'func': 'core.tasks.generate_scheduled_report_task',
            'args': str(sched_report.pk),
            'schedule_type': q_freq,
            'repeats': -1,
        }
        if q_freq == Schedule.CRON:
            defaults['cron'] = sched_report.cron_expression
        else:
            defaults['cron'] = ''

        # Configure next_run if start_time is set
        if sched_report.start_time:
            now = timezone.now()
            # Compute next run date with this start time
            next_date = now.date()
            next_run = timezone.make_aware(
                datetime.datetime.combine(next_date, sched_report.start_time),
                timezone.get_current_timezone()
            )
            if next_run < now:
                # If the time has already passed today, set to tomorrow
                next_run += datetime.timedelta(days=1)
            defaults['next_run'] = next_run

        q_schedule, created = Schedule.objects.update_or_create(
            name=f"scheduled_report_{sched_report.pk}",
            defaults=defaults
        )
        if sched_report.schedule != q_schedule:
            sched_report.schedule = q_schedule
            sched_report.save(update_fields=['schedule'])
    else:
        if sched_report.schedule:
            q_sched = sched_report.schedule
            sched_report.schedule = None
            sched_report.save(update_fields=['schedule'])
            q_sched.delete()


@method_decorator(login_required, name='dispatch')
class ScheduledReportCreateView(ObjectEditView):
    queryset = ScheduledReport.objects.all()
    model_form = ScheduledReportForm
    template_name = 'core/reports/report_schedule_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Schedule a Report'
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        handle_report_scheduling(self.object)
        return response


@method_decorator(login_required, name='dispatch')
class ScheduledReportUpdateView(ObjectEditView):
    queryset = ScheduledReport.objects.all()
    model_form = ScheduledReportForm
    template_name = 'core/reports/report_schedule_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f"Edit Schedule: {self.object.name}"
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        handle_report_scheduling(self.object)
        return response


@method_decorator(login_required, name='dispatch')
class ScheduledReportDeleteView(ObjectDeleteView):
    queryset = ScheduledReport.objects.all()
    template_name = 'core/reports/report_schedule_confirm_delete.html'


@method_decorator(login_required, name='dispatch')
class ReportTriggerImmediateView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = ('core.view_scheduledreport',)

    def has_permission(self):
        perms = self.get_permission_required()
        obj = None
        try:
            obj = get_object_or_404(ScheduledReport, pk=self.kwargs.get('pk'))
        except Exception:
            pass
        return self.request.user.has_perms(perms, obj=obj)

    def post(self, request, pk):
        sched = get_object_or_404(ScheduledReport, pk=pk)
        
        # Trigger report generation synchronously for immediate visual feedback in the UI
        from core.tasks import generate_scheduled_report_task
        success = generate_scheduled_report_task(sched.pk)
        if success:
            messages.success(request, f"Scheduled report '{sched.name}' generated and sent successfully.")
        else:
            sched.refresh_from_db()
            error_msg = sched.last_status or "Check logs."
            messages.error(request, f"Failed to generate scheduled report '{sched.name}': {error_msg}")
                
        return redirect(request.POST.get('return_url') or reverse('scheduledreport_list'))


@method_decorator(login_required, name='dispatch')
class ReportTemplatePreviewView(PermissionRequiredMixin, View):
    permission_required = ('core.view_reporttemplate',)
    def post(self, request, *args, **kwargs):
        report_type = request.POST.get('report_type')
        style_preset = request.POST.get('style_preset', 'default')
        included_columns = request.POST.getlist('included_columns')
        include_summary_cards = request.POST.get('include_summary_cards') == 'on' or request.POST.get('include_summary_cards') == 'true'
        include_distribution_chart = request.POST.get('include_distribution_chart') == 'on' or request.POST.get('include_distribution_chart') == 'true'
        group_by_field = request.POST.get('group_by_field', '')
        advanced_mode = request.POST.get('advanced_mode') == 'on' or request.POST.get('advanced_mode') == 'true'
        template_content = request.POST.get('template_content', '')
        name = request.POST.get('name', 'Preview Report')
        description = request.POST.get('description', '')

        # Resolve active tenant for preview scoping
        selected_tenant_id = request.POST.get('tenant')
        active_tenant = None
        if selected_tenant_id and request.user.is_superuser:
            from organization.models import Tenant
            active_tenant = Tenant.objects.filter(pk=selected_tenant_id).first()
        else:
            from core.managers import get_current_tenant
            active_tenant = get_current_tenant()

        # Resolve multi-tenant filter scoping constellation for preview
        selected_filter_tenant_ids = request.POST.getlist('filter_tenants')
        filter_tenants = []
        if selected_filter_tenant_ids and request.user.is_superuser:
            from organization.models import Tenant
            filter_tenants = list(Tenant.objects.filter(pk__in=selected_filter_tenant_ids))

        # Create dynamic in-memory ReportTemplate object
        template_instance = ReportTemplate(
            name=name,
            description=description,
            report_type=report_type,
            included_columns=included_columns,
            include_summary_cards=include_summary_cards,
            include_distribution_chart=include_distribution_chart,
            group_by_field=group_by_field,
            style_preset=style_preset,
            advanced_mode=advanced_mode,
            template_content=template_content
        )

        from core.reports import compile_report_context, get_polished_system_html_template
        
        try:
            headers, rows, summary_cards, grouped_data, chart_svg, context_data = compile_report_context(
                template_instance, active_tenant=active_tenant, filter_tenants=filter_tenants
            )
            
            if advanced_mode and template_content.strip():
                # Sandbox or legacy Django render
                try:
                    from jinja2.sandbox import SandboxedEnvironment
                    env = SandboxedEnvironment()
                    jinja_template = env.from_string(template_content)
                    if report_type == ReportTemplate.REPORT_TYPE_ASSET_SUMMARY:
                        context_data.update({
                            'total_assets': len(rows),
                            'acquisition_sum': sum(float(r[headers[8]].replace('$', '').replace(',', '')) for r in rows if headers[8] in r and r[headers[8]] != '-') if len(headers) > 8 else 0,
                            'location_distribution': [{'location': k, 'count': len(v)} for k, v in grouped_data.items()]
                        })
                    rendered_html = jinja_template.render(context_data)
                except Exception as je:
                    logger.exception(f"Jinja2 sandboxed render failed: {je}")
                    raise je
            else:
                html_template_str = get_polished_system_html_template()
                django_template = Template(html_template_str)
                context_data['request'] = request
                rendered_html = django_template.render(Context(context_data))
            
            return HttpResponse(rendered_html)
        except Exception as e:
            logger.exception("Template Render Error in preview")
            return HttpResponse(f"<h3>Template Render Error:</h3><pre>{str(e)}</pre>", status=400)


@method_decorator(login_required, name='dispatch')
class ReportTemplateDownloadView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = ('core.view_reporttemplate',)

    def has_permission(self):
        perms = self.get_permission_required()
        obj = None
        try:
            obj = get_object_or_404(ReportTemplate, pk=self.kwargs.get('pk'))
        except Exception:
            pass
        return self.request.user.has_perms(perms, obj=obj)

    def get(self, request, pk, *args, **kwargs):
        # objects automatically handles tenant scoping!
        template = get_object_or_404(ReportTemplate.objects.all(), pk=pk)
        
        # Enforce multi-tenant thread-local active tenant binding
        from core.managers import get_current_tenant
        active_tenant = get_current_tenant()
        
        # Enforce sandboxed constellation
        filter_tenants = list(template.filter_tenants.all())
        
        from core.reports import compile_report_context, get_polished_system_html_template
        
        try:
            headers, rows, summary_cards, grouped_data, chart_svg, context_data = compile_report_context(
                template, active_tenant=active_tenant, filter_tenants=filter_tenants
            )
            
            format_type = request.GET.get('format', 'html').lower()
            
            if format_type == 'csv':
                import io
                import csv
                csv_buffer = io.StringIO()
                writer = csv.writer(csv_buffer)
                
                # Write headers
                writer.writerow(headers)
                # Write rows in sequence
                for r in rows:
                    writer.writerow([r.get(head, '-') for head in headers])
                    
                response = HttpResponse(csv_buffer.getvalue(), content_type='text/csv')
                filename = f"{template.name.lower().replace(' ', '_')}_{timezone.now():%Y%m%d}.csv"
                response['Content-Disposition'] = f'attachment; filename="{filename}"'
                return response
                
            else:
                # HTML compiler
                if template.advanced_mode and template.template_content.strip():
                    try:
                        from jinja2.sandbox import SandboxedEnvironment
                        env = SandboxedEnvironment()
                        jinja_template = env.from_string(template.template_content)
                        if template.report_type == ReportTemplate.REPORT_TYPE_ASSET_SUMMARY:
                            context_data.update({
                                'total_assets': len(rows),
                                'acquisition_sum': sum(float(r[headers[8]].replace('$', '').replace(',', '')) for r in rows if headers[8] in r and r[headers[8]] != '-') if len(headers) > 8 else 0,
                                'location_distribution': [{'location': k, 'count': len(v)} for k, v in grouped_data.items()]
                            })
                        rendered_html = jinja_template.render(context_data)
                    except Exception as je:
                        logger.exception(f"Jinja2 sandboxed render failed: {je}")
                        raise je
                else:
                    html_template_str = get_polished_system_html_template()
                    django_template = Template(html_template_str)
                    context_data['request'] = request
                    rendered_html = django_template.render(Context(context_data))
                    
                response = HttpResponse(rendered_html, content_type='text/html')
                filename = f"{template.name.lower().replace(' ', '_')}_{timezone.now():%Y%m%d}.html"
                if request.GET.get('print') == 'true':
                    response['Content-Disposition'] = f'inline; filename="{filename}"'
                else:
                    response['Content-Disposition'] = f'attachment; filename="{filename}"'
                return response
        except Exception as e:
            logger.exception("Template Render Error in download")
            return HttpResponse(f"<h3>Template Render Error:</h3><pre>{str(e)}</pre>", status=400)


