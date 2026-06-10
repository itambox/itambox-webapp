import json
import logging
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.utils.decorators import method_decorator
from django.views.generic import View

from extras.models import AlertRule, AlertLog, NotificationChannel
from core.tables import AlertRuleTable, AlertLogTable, NotificationChannelTable
from core.forms import AlertRuleForm, NotificationChannelForm
from core.filters import AlertLogFilterSet
from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView
)
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
    permission_required = ('core.change_alertrule',)

    def perform_action(self, rule, request):
        from django_q.tasks import async_task
        rule_id = rule.pk
        async_task('core.tasks.run_alert_rule_now', rule_id)
        return {'message': f"Evaluation queued for '{rule.name}'. New alerts will appear shortly."}

    def get_success_redirect(self, obj, result):
        return redirect(
            self.request.POST.get('return_url') or reverse('alert_rule_detail', kwargs={'pk': obj.pk})
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
    permission_required = ('core.change_alertlog',)

    def perform_action(self, alert, request):
        if alert.status == AlertLog.STATUS_ACTIVE:
            alert.status = AlertLog.STATUS_ACKNOWLEDGED
            alert.acknowledged_by = request.user
            alert.save(update_fields=['status', 'acknowledged_by'])
        return {'message': f"Alert '{alert.subject}' acknowledged."}

    def get_success_redirect(self, obj, result):
        return redirect(
            self.request.POST.get('return_url') or reverse('alertlog_list')
        )


class AlertResolveView(SimplePostView):
    queryset = AlertLog.objects.all()
    permission_required = ('core.change_alertlog',)

    def perform_action(self, alert, request):
        if alert.status in [AlertLog.STATUS_ACTIVE, AlertLog.STATUS_ACKNOWLEDGED]:
            alert.status = AlertLog.STATUS_RESOLVED
            alert.resolved_by = request.user
            alert.resolved_at = timezone.now()
            alert.save(update_fields=['status', 'resolved_by', 'resolved_at'])
        return {'message': f"Alert '{alert.subject}' marked as resolved."}

    def get_success_redirect(self, obj, result):
        return redirect(
            self.request.POST.get('return_url') or reverse('alertlog_list')
        )


class _BulkAlertActionView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Apply a status transition to many AlertLogs selected in the Alert Center.

    Reads the checked ``pk`` list (gathered by batch-actions.ts) and transitions
    eligible logs. Tenant-scoped: AlertLog.objects only exposes the current
    tenant's logs, so a user can never act on another tenant's alerts.
    """
    permission_required = ('core.change_alertlog',)
    hx_trigger = 'tableRefreshRequired'
    eligible_statuses = ()

    def apply(self, queryset, user):
        raise NotImplementedError

    def success_message(self, count):
        raise NotImplementedError

    def post(self, request, *args, **kwargs):
        pks = request.POST.getlist('pk')
        return_url = request.POST.get('return_url') or reverse('alertlog_list')

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
    permission_required = ('core.change_notificationchannel',)

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
        return redirect(reverse('notificationchannel_list'))
