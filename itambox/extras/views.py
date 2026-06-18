import json

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count
from django.http import HttpResponse, QueryDict
from django.utils.http import urlencode
from django.views.generic import View
from core.managers import get_current_tenant
from .models import Tag, CustomField, CustomFieldset, SavedFilter, ConfigContext
from .forms import TagForm, TagFilterForm, CustomFieldForm, CustomFieldFilterForm, CustomFieldsetForm, CustomFieldsetFilterForm, SavedFilterForm, SavedFilterFilterForm, ConfigContextForm, ConfigContextFilterSet, ConfigContextFilterForm, ConfigContextTable
from django_tables2 import RequestConfig
from .tables import TagTable, CustomFieldTable, CustomFieldsetTable, SavedFilterTable
from .filters import TagFilter, CustomFieldFilterSet, CustomFieldsetFilterSet, SavedFilterFilterSet
from itambox.utils import get_paginate_count, get_model_viewname # Import the utility function
from assets.tables import AssetTable # Import AssetTable
from users.models import UserPreference # Import UserPreference
from django.urls import reverse, reverse_lazy
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib import messages
from itambox.views.generic import ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView, ObjectBulkEditView, ObjectBulkDeleteView
from itambox.panels import Panel

class TagDetailView(ObjectDetailView):
    queryset = Tag.objects.all()
    template_name = 'extras/tags/tag_detail.html'

    layout = (
        ((Panel('info', 'Tag Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tag = self.object

        # Fetch related assets using the related_name from Asset.tags
        related_assets = tag.assets.all()

        # Create and configure the assets table
        assets_table = AssetTable(related_assets, request=self.request)
        # Disable pagination for related table
        assets_table.configure(self.request, paginate=False)

        context['assets_table'] = assets_table
        return context

class TagCreateView(ObjectEditView):
    model_form = TagForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'extras:tag_list'

class TagUpdateView(ObjectEditView):
    queryset = Tag.objects.all()
    model_form = TagForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'extras:tag_list'

class TagDeleteView(ObjectDeleteView):
    queryset = Tag.objects.all()
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('extras:tag_list')

# Refactor tag_list to CBV
class TagListView(ObjectListView):
    queryset = Tag.objects.all()
    filterset = TagFilter
    filterset_form = TagFilterForm # Assuming TagFilterForm exists
    table = TagTable
    action_buttons = ('add',) # Add create button
    template_name = 'generic/object_list.html' # Use base template


class TagBulkEditView(ObjectBulkEditView):
    queryset = Tag.objects.all()


class TagBulkDeleteView(ObjectBulkDeleteView):
    queryset = Tag.objects.all()


# Custom Fields
class CustomFieldListView(ObjectListView):
    queryset = CustomField.objects.all()
    filterset = CustomFieldFilterSet
    filterset_form = CustomFieldFilterForm
    table = CustomFieldTable
    action_buttons = ('add',)


class CustomFieldDetailView(ObjectDetailView):
    queryset = CustomField.objects.all()

    layout = (
        ((Panel('info', 'Custom Field Details'),),),
    )


class CustomFieldEditView(ObjectEditView):
    queryset = CustomField.objects.all()
    model = CustomField
    model_form = CustomFieldForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'extras:customfield_list'


class CustomFieldDeleteView(ObjectDeleteView):
    queryset = CustomField.objects.all()
    model = CustomField
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('extras:customfield_list')


class CustomFieldBulkEditView(ObjectBulkEditView):
    queryset = CustomField.objects.all()


class CustomFieldBulkDeleteView(ObjectBulkDeleteView):
    queryset = CustomField.objects.all()


# Custom Fieldsets
class CustomFieldsetListView(ObjectListView):
    queryset = CustomFieldset.objects.annotate(fields_count=Count('fields'))
    filterset = CustomFieldsetFilterSet
    filterset_form = CustomFieldsetFilterForm
    table = CustomFieldsetTable
    action_buttons = ('add',)


class CustomFieldsetDetailView(ObjectDetailView):
    queryset = CustomFieldset.objects.all().prefetch_related('fields', 'asset_types')

    layout = (
        ((Panel('info', 'Custom Field Set Details'),),),
    )


class CustomFieldsetEditView(ObjectEditView):
    queryset = CustomFieldset.objects.all()
    model = CustomFieldset
    model_form = CustomFieldsetForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'extras:customfieldset_list'


class CustomFieldsetDeleteView(ObjectDeleteView):
    queryset = CustomFieldset.objects.all()
    model = CustomFieldset
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('extras:customfieldset_list')


class CustomFieldsetBulkEditView(ObjectBulkEditView):
    queryset = CustomFieldset.objects.all()


class CustomFieldsetBulkDeleteView(ObjectBulkDeleteView):
    queryset = CustomFieldset.objects.all()


# =============================================================================
# Saved Filters
# =============================================================================

class SavedFilterListView(ObjectListView):
    queryset = SavedFilter.objects.select_related('content_type', 'tenant', 'created_by')
    filterset = SavedFilterFilterSet
    filterset_form = SavedFilterFilterForm
    table = SavedFilterTable
    action_buttons = ('add',)
    template_name = 'generic/object_list.html'


class SavedFilterDetailView(ObjectDetailView):
    queryset = SavedFilter.objects.all()

    layout = (
        ((Panel('info', 'Saved Filter Details'),),),
    )


class SavedFilterEditView(ObjectEditView):
    queryset = SavedFilter.objects.all()
    model = SavedFilter
    model_form = SavedFilterForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'extras:savedfilter_list'


class SavedFilterDeleteView(ObjectDeleteView):
    queryset = SavedFilter.objects.all()
    model = SavedFilter
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('extras:savedfilter_list')


class SavedFilterSaveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Quick-save the current list-view filter as a named SavedFilter.

    POST-only. The list view's filter offcanvas hx-includes the filter form
    (``.filter-form-sidebar``), so the POST carries the filter fields' CURRENT
    values (whether or not "Apply" was clicked) alongside the save controls.
    We persist those filter params and redirect back to the list with
    ``?filter=<new pk>`` so the freshly saved filter applies immediately.

    Save-control fields use an ``sf_`` prefix so they never collide with a
    filterset field of the same name (e.g. a model whose filter has ``name``).
    """
    permission_required = ('extras.add_savedfilter',)

    # POST keys that are save-form controls or list chrome, NOT filter params.
    NON_FILTER_PARAMS = frozenset({
        'sf_name', 'sf_shared', 'sf_is_global', 'model', 'return_url',
        'csrfmiddlewaretoken', 'page', 'per_page', 'sort', 'deleted', 'filter',
    })

    def post(self, request, *args, **kwargs):
        name = (request.POST.get('sf_name') or '').strip()
        model_str = (request.POST.get('model') or '').strip()
        is_global = request.POST.get('sf_is_global') in ('1', 'true', 'on', 'yes')
        shared = request.POST.get('sf_shared') in ('1', 'true', 'on', 'yes')

        content_type = self._resolve_content_type(model_str)
        if not name or content_type is None:
            return self._respond(request, model_str, None,
                                  "Provide a name and a valid model to save the filter.")

        parameters = self._parse_parameters(request.POST)

        tenant = get_current_tenant()
        if is_global and request.user.is_superuser:
            tenant = None

        saved = SavedFilter.objects.create(
            name=name,
            content_type=content_type,
            parameters=parameters,
            shared=shared,
            created_by=request.user,
            tenant=tenant,
        )

        return self._respond(request, model_str, saved.pk, None)

    def _resolve_content_type(self, model_str):
        if '.' not in model_str:
            return None
        app_label, model_name = model_str.split('.', 1)
        try:
            return ContentType.objects.get_by_natural_key(app_label, model_name)
        except ContentType.DoesNotExist:
            return None

    def _parse_parameters(self, post):
        """Filter params = POST minus control/chrome keys and empty values."""
        params = {}
        for key in post.keys():
            if key in self.NON_FILTER_PARAMS:
                continue
            values = [v for v in post.getlist(key) if v not in (None, '')]
            if not values:
                continue
            params[key] = values if len(values) > 1 else values[0]
        return params

    def _list_url(self, request, model_str):
        return_url = request.POST.get('return_url')
        if return_url:
            return return_url.split('?', 1)[0]
        content_type = self._resolve_content_type(model_str)
        if content_type is not None:
            model = content_type.model_class()
            if model is not None:
                try:
                    return reverse(get_model_viewname(model, 'list'))
                except Exception:
                    pass
        return reverse('extras:savedfilter_list')

    def _respond(self, request, model_str, pk, error):
        """Redirect to the list (with ?filter=<pk> on success). HTMX submissions
        get a 204 + HX-Redirect so the browser performs a full navigation and the
        list's ?filter load hook re-applies the saved filter."""
        list_url = self._list_url(request, model_str)
        target = f"{list_url}?{urlencode({'filter': pk})}" if pk else list_url
        if error:
            messages.error(request, error)
        if request.headers.get('HX-Request') == 'true':
            response = HttpResponse(status=204)
            response['HX-Redirect'] = target
            return response
        return redirect(target)


# =============================================================================
# Config Context Views
# =============================================================================

class ConfigContextListView(ObjectListView):
    queryset = ConfigContext.objects.all()
    filterset = ConfigContextFilterSet
    filterset_form = ConfigContextFilterForm
    table = ConfigContextTable
    action_buttons = ('add',)
    template_name = 'generic/object_list.html'


class ConfigContextCreateView(ObjectEditView):
    model_form = ConfigContextForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'extras:configcontext_list'


class ConfigContextEditView(ObjectEditView):
    queryset = ConfigContext.objects.all()
    model_form = ConfigContextForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'extras:configcontext_list'


class ConfigContextDeleteView(ObjectDeleteView):
    queryset = ConfigContext.objects.all()
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('extras:configcontext_list')


# =============================================================================
# Alerting Views
# =============================================================================
import logging

from django.utils import timezone
from django.utils.decorators import method_decorator

from .models import AlertRule, AlertLog, NotificationChannel, ReportTemplate, ScheduledReport
from .tables import (
    AlertRuleTable, AlertLogTable, NotificationChannelTable,
    ReportTemplateTable, ScheduledReportTable,
)
from .forms import (
    AlertRuleForm, NotificationChannelForm, ReportTemplateForm, ScheduledReportForm,
)
from .filters import AlertLogFilterSet
from itambox.views.generic.service_views import SimplePostView

logger = logging.getLogger(__name__)


@method_decorator(login_required, name='dispatch')
class AlertRuleListView(ObjectListView):
    queryset = AlertRule.objects.all()
    table = AlertRuleTable
    template_name = 'core/alerts/alert_rule_list.html'
    action_buttons = ('add',)

    def get_breadcrumbs(self):
        return [
            (reverse('dashboard'), 'Dashboard'),
            (None, 'Alert Rules')
        ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Alert Rules'
        return context


@method_decorator(login_required, name='dispatch')
class AlertRuleDetailView(ObjectDetailView):
    queryset = AlertRule.objects.all()
    template_name = 'core/alerts/alert_rule_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obj = self.get_object()
        context['title'] = f"Alert Rule: {obj.name}"
        context['logs_count'] = obj.logs.count()
        context['active_logs_count'] = obj.logs.filter(status='active').count()
        return context


@method_decorator(login_required, name='dispatch')
class AlertRuleCreateView(ObjectEditView):
    queryset = AlertRule.objects.all()
    model_form = AlertRuleForm
    template_name = 'core/alerts/alert_rule_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Create Alert Rule'
        return context


@method_decorator(login_required, name='dispatch')
class AlertRuleUpdateView(ObjectEditView):
    queryset = AlertRule.objects.all()
    model_form = AlertRuleForm
    template_name = 'core/alerts/alert_rule_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f"Edit Alert Rule: {self.object.name}"
        return context


@method_decorator(login_required, name='dispatch')
class AlertRuleDeleteView(ObjectDeleteView):
    queryset = AlertRule.objects.all()
    template_name = 'core/alerts/alert_rule_confirm_delete.html'


class AlertRuleRunNowView(SimplePostView):
    """Evaluate a single alert rule immediately, on demand.

    The evaluation is enqueued as a background task rather than run inline:
    run_alert_rule_now() deliberately clears tenant/user contextvars (it is
    designed to run standalone in a worker), so running it inside the request
    would contaminate the request's context for the remainder of the response.
    """
    queryset = AlertRule.objects.all()
    permission_required = ('extras.change_alertrule',)

    def perform_action(self, rule, request):
        from django_q.tasks import async_task
        rule_id = rule.pk
        async_task('core.tasks.run_alert_rule_now', rule_id)
        return {'message': f"Evaluation queued for '{rule.name}'. New alerts will appear shortly."}

    def get_success_redirect(self, obj, result):
        return redirect(
            self.request.POST.get('return_url') or reverse('extras:alertrule_detail', kwargs={'pk': obj.pk})
        )


@method_decorator(login_required, name='dispatch')
class AlertLogListView(ObjectListView):
    queryset = AlertLog.objects.select_related('rule', 'content_type').order_by('-created_at')
    table = AlertLogTable
    template_name = 'core/alerts/alert_list.html'
    action_buttons = ()
    filterset_class = AlertLogFilterSet

    def get_breadcrumbs(self):
        return [
            (reverse('dashboard'), 'Dashboard'),
            (None, 'Alerts Center')
        ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Alerts Center'

        from core.managers import get_current_tenant
        current_tenant = get_current_tenant()

        active_qs = AlertLog.objects.filter(status=AlertLog.STATUS_ACTIVE)
        acknowledged_qs = AlertLog.objects.filter(status=AlertLog.STATUS_ACKNOWLEDGED)

        if current_tenant:
            active_qs = active_qs.filter(tenant=current_tenant)
            acknowledged_qs = acknowledged_qs.filter(tenant=current_tenant)

        context['active_alerts_count'] = active_qs.count()
        context['acknowledged_alerts_count'] = acknowledged_qs.count()
        return context


class AlertAcknowledgeView(SimplePostView):
    queryset = AlertLog.objects.all()
    permission_required = ('extras.change_alertlog',)

    def perform_action(self, alert, request):
        if alert.status == AlertLog.STATUS_ACTIVE:
            alert.status = AlertLog.STATUS_ACKNOWLEDGED
            alert.acknowledged_by = request.user
            alert.save(update_fields=['status', 'acknowledged_by'])
        return {'message': f"Alert '{alert.subject}' acknowledged."}

    def get_success_redirect(self, obj, result):
        return redirect(
            self.request.POST.get('return_url') or reverse('extras:alertlog_list')
        )


class AlertResolveView(SimplePostView):
    queryset = AlertLog.objects.all()
    permission_required = ('extras.change_alertlog',)

    def perform_action(self, alert, request):
        if alert.status in [AlertLog.STATUS_ACTIVE, AlertLog.STATUS_ACKNOWLEDGED]:
            alert.status = AlertLog.STATUS_RESOLVED
            alert.resolved_by = request.user
            alert.resolved_at = timezone.now()
            alert.save(update_fields=['status', 'resolved_by', 'resolved_at'])
        return {'message': f"Alert '{alert.subject}' marked as resolved."}

    def get_success_redirect(self, obj, result):
        return redirect(
            self.request.POST.get('return_url') or reverse('extras:alertlog_list')
        )


class _BulkAlertActionView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Apply a status transition to many AlertLogs selected in the Alert Center.

    Reads the checked ``pk`` list (gathered by batch-actions.ts) and transitions
    eligible logs. Tenant-scoped: AlertLog.objects only exposes the current
    tenant's logs, so a user can never act on another tenant's alerts.
    """
    permission_required = ('extras.change_alertlog',)
    hx_trigger = 'tableRefreshRequired'
    eligible_statuses = ()

    def apply(self, queryset, user):
        raise NotImplementedError

    def success_message(self, count):
        raise NotImplementedError

    def post(self, request, *args, **kwargs):
        pks = request.POST.getlist('pk')
        return_url = request.POST.get('return_url') or reverse('extras:alertlog_list')

        if not pks:
            return self._respond(request, "No alerts selected.", 'warning', return_url)

        qs = AlertLog.objects.filter(pk__in=pks)
        if self.eligible_statuses:
            qs = qs.filter(status__in=self.eligible_statuses)
        count = self.apply(qs, request.user)
        return self._respond(request, self.success_message(count), 'success', return_url)

    def _respond(self, request, message, level, return_url):
        if getattr(request, 'htmx', False):
            resp = HttpResponse(status=204)
            resp['HX-Trigger'] = json.dumps({
                self.hx_trigger: None,
                'showMessage': {'message': message, 'level': level},
            })
            return resp
        getattr(messages, level)(request, message)
        return redirect(return_url)


class AlertBulkAcknowledgeView(_BulkAlertActionView):
    eligible_statuses = (AlertLog.STATUS_ACTIVE,)

    def apply(self, queryset, user):
        return queryset.update(status=AlertLog.STATUS_ACKNOWLEDGED, acknowledged_by=user)

    def success_message(self, count):
        return f"{count} alert(s) acknowledged."


class AlertBulkResolveView(_BulkAlertActionView):
    eligible_statuses = (AlertLog.STATUS_ACTIVE, AlertLog.STATUS_ACKNOWLEDGED)

    def apply(self, queryset, user):
        return queryset.update(
            status=AlertLog.STATUS_RESOLVED,
            resolved_by=user,
            resolved_at=timezone.now(),
        )

    def success_message(self, count):
        return f"{count} alert(s) resolved."


@method_decorator(login_required, name='dispatch')
class NotificationChannelListView(ObjectListView):
    queryset = NotificationChannel.objects.all()
    table = NotificationChannelTable
    template_name = 'core/alerts/notificationchannel_list.html'
    action_buttons = ('add',)

    def get_breadcrumbs(self):
        return [
            (reverse('dashboard'), 'Dashboard'),
            (None, 'Notification Channels')
        ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Notification Channels'
        return context


@method_decorator(login_required, name='dispatch')
class NotificationChannelCreateView(ObjectEditView):
    queryset = NotificationChannel.objects.all()
    model_form = NotificationChannelForm
    template_name = 'core/alerts/notificationchannel_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Create Notification Channel'
        return context


@method_decorator(login_required, name='dispatch')
class NotificationChannelUpdateView(ObjectEditView):
    queryset = NotificationChannel.objects.all()
    model_form = NotificationChannelForm
    template_name = 'core/alerts/notificationchannel_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f"Edit Notification Channel: {self.object.name}"
        return context


@method_decorator(login_required, name='dispatch')
class NotificationChannelDeleteView(ObjectDeleteView):
    queryset = NotificationChannel.objects.all()
    template_name = 'core/alerts/notificationchannel_confirm_delete.html'


class NotificationChannelTestView(SimplePostView):
    """Send a test notification through a channel and report success/failure inline."""
    queryset = NotificationChannel.objects.all()
    permission_required = ('extras.change_notificationchannel',)

    def perform_action(self, channel, request):
        from core.events import send_notification_to_channel
        ok = send_notification_to_channel(
            channel,
            subject="ITAMbox Test Notification",
            body=f"This is a test message sent to channel '{channel.name}' ({channel.get_channel_type_display()}).",
        )
        if ok:
            return {'message': f"Test notification sent successfully via '{channel.name}'."}
        raise Exception(f"Channel '{channel.name}' returned a delivery failure.")

    def get_success_redirect(self, obj, result):
        return redirect(reverse('extras:notificationchannel_list'))


# =============================================================================
# Reporting Views
# =============================================================================

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
    permission_required = ('extras.view_scheduledreport',)

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

        return redirect(request.POST.get('return_url') or reverse('extras:scheduledreport_list'))


@method_decorator(login_required, name='dispatch')
class ReportTemplatePreviewView(PermissionRequiredMixin, View):
    permission_required = ('extras.view_reporttemplate',)
    def post(self, request, *args, **kwargs):
        from django.template import Template, Context
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
    permission_required = ('extras.view_reporttemplate',)

    def has_permission(self):
        perms = self.get_permission_required()
        obj = None
        try:
            obj = get_object_or_404(ReportTemplate, pk=self.kwargs.get('pk'))
        except Exception:
            pass
        return self.request.user.has_perms(perms, obj=obj)

    def get(self, request, pk, *args, **kwargs):
        from django.template import Template, Context
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

