import django_filters
from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django.contrib.auth import get_user_model
from core.filters import BaseFilterSet
from django.contrib.contenttypes.models import ContentType
from .models import (
    Tag, CustomField, CustomFieldset, SavedFilter, AlertLog, AlertRule,
    EventRule, WebhookEndpoint, NotificationChannel, JournalEntry,
    ReportTemplate, ScheduledReport,
)

class TagFilter(django_filters.FilterSet):
    q = django_filters.CharFilter(
        method='search',
        label=_('Search'),
    )
    
    class Meta:
        model = Tag
        fields = ['name']
    
    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) | 
            Q(slug__icontains=value) |
            Q(description__icontains=value)
        )


class CustomFieldFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(method='search', label=_('Search'))

    class Meta:
        model = CustomField
        fields = ['name', 'label', 'field_type', 'required']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(label__icontains=value)
        ).distinct()


class CustomFieldsetFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(method='search', label=_('Search'))

    class Meta:
        model = CustomFieldset
        fields = ['name']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value)
        ).distinct()


class SavedFilterFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(method='search', label=_('Search'))
    content_type = django_filters.ModelChoiceFilter(
        queryset=ContentType.objects.order_by('app_label', 'model'),
        label=_('Object Type'),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    shared = django_filters.BooleanFilter(
        label=_('Shared'),
        widget=forms.Select(choices=[('', '---------'), (True, 'Yes'), (False, 'No')], attrs={'class': 'form-select'}),
    )
    enabled = django_filters.BooleanFilter(
        label=_('Enabled'),
        widget=forms.Select(choices=[('', '---------'), (True, 'Yes'), (False, 'No')], attrs={'class': 'form-select'}),
    )

    class Meta:
        model = SavedFilter
        fields = ['content_type', 'shared', 'enabled']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(description__icontains=value)
        ).distinct()


class EventRuleFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(method='search', label=_('Search'))

    class Meta:
        model = EventRule
        fields = ['name', 'action_type', 'enabled']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(Q(name__icontains=value)).distinct()


class WebhookEndpointFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(method='search', label=_('Search'))

    class Meta:
        model = WebhookEndpoint
        fields = ['name', 'http_method', 'enabled']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(url__icontains=value)
        ).distinct()


class NotificationChannelFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(method='search', label=_('Search'))

    class Meta:
        model = NotificationChannel
        fields = ['name', 'channel_type', 'enabled']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(Q(name__icontains=value)).distinct()


class AlertRuleFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(method='search', label=_('Search'))

    class Meta:
        model = AlertRule
        fields = ['name', 'alert_type', 'severity', 'is_active', 'is_muted']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(description__icontains=value)
        ).distinct()


class AlertLogFilterSet(BaseFilterSet):
    status = django_filters.MultipleChoiceFilter(
        choices=AlertLog.STATUS_CHOICES,
        label=_('Status'),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )
    severity = django_filters.MultipleChoiceFilter(
        choices=AlertRule.SEVERITY_CHOICES,
        label=_('Severity'),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )
    rule = django_filters.ModelChoiceFilter(
        queryset=AlertRule.objects.all(),
        label=_('Rule'),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    created_after = django_filters.DateFilter(
        field_name='created_at',
        lookup_expr='gte',
        label=_('Created after'),
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
    )
    created_before = django_filters.DateFilter(
        field_name='created_at',
        lookup_expr='lte',
        label=_('Created before'),
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
    )

    class Meta:
        model = AlertLog
        fields = ['status', 'severity', 'rule', 'created_after', 'created_before']


class JournalEntryFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(
        method='search',
        label=_('Search'),
        widget=forms.TextInput(attrs={'placeholder': _('Search comments…')}),
    )
    model = django_filters.ModelMultipleChoiceFilter(
        field_name='model',
        queryset=ContentType.objects.order_by('app_label', 'model'),
        label=_('Object Type'),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )
    user = django_filters.ModelChoiceFilter(
        queryset=get_user_model().objects.order_by('username'),
        label=_('User'),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    created_after = django_filters.DateTimeFilter(
        field_name='created',
        lookup_expr='gte',
        label=_('After'),
        widget=forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
    )
    created_before = django_filters.DateTimeFilter(
        field_name='created',
        lookup_expr='lte',
        label=_('Before'),
        widget=forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
    )

    class Meta:
        model = JournalEntry
        fields = ['model', 'user', 'object_id']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(Q(comment__icontains=value)).distinct()


class ReportTemplateFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(method='search', label=_('Search'))

    class Meta:
        model = ReportTemplate
        fields = ['report_type', 'style_preset', 'advanced_mode']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(description__icontains=value)
        ).distinct()


class ScheduledReportFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(method='search', label=_('Search'))

    class Meta:
        model = ScheduledReport
        fields = ['report', 'frequency', 'format', 'is_active']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(recipients__icontains=value)
        ).distinct()
