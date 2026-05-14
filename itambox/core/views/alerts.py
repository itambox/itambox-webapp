import logging
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import View
from django.contrib import messages
from django.utils import timezone
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.utils.decorators import method_decorator
from django.contrib.auth.decorators import login_required
from django.urls import reverse

from core.models import AlertRule, AlertLog, NotificationChannel
from core.tables import AlertRuleTable, AlertLogTable, NotificationChannelTable
from core.forms import AlertRuleForm, NotificationChannelForm
from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView
)

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


@method_decorator(login_required, name='dispatch')
class AlertLogListView(ObjectListView):
    queryset = AlertLog.objects.select_related('rule', 'content_type').order_by('-created_at')
    table = AlertLogTable
    template_name = 'core/alerts/alert_list.html'
    action_buttons = ()

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


@method_decorator(login_required, name='dispatch')
class AlertAcknowledgeView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = ('core.change_alertlog',)

    def has_permission(self):
        perms = self.get_permission_required()
        obj = None
        try:
            obj = get_object_or_404(AlertLog, pk=self.kwargs.get('pk'))
        except Exception:
            pass
        return self.request.user.has_perms(perms, obj=obj)

    def post(self, request, pk):
        alert = get_object_or_404(AlertLog, pk=pk)
        if alert.status == AlertLog.STATUS_ACTIVE:
            alert.status = AlertLog.STATUS_ACKNOWLEDGED
            alert.save()
            messages.success(request, f"Alert '{alert.subject}' has been acknowledged.")
        return redirect(request.POST.get('return_url') or reverse('alertlog_list'))


@method_decorator(login_required, name='dispatch')
class AlertResolveView(PermissionRequiredMixin, LoginRequiredMixin, View):
    permission_required = ('core.change_alertlog',)

    def has_permission(self):
        perms = self.get_permission_required()
        obj = None
        try:
            obj = get_object_or_404(AlertLog, pk=self.kwargs.get('pk'))
        except Exception:
            pass
        return self.request.user.has_perms(perms, obj=obj)

    def post(self, request, pk):
        alert = get_object_or_404(AlertLog, pk=pk)
        if alert.status in [AlertLog.STATUS_ACTIVE, AlertLog.STATUS_ACKNOWLEDGED]:
            alert.status = AlertLog.STATUS_RESOLVED
            alert.resolved_at = timezone.now()
            alert.save()
            messages.success(request, f"Alert '{alert.subject}' has been marked as resolved.")
        return redirect(request.POST.get('return_url') or reverse('alertlog_list'))


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
