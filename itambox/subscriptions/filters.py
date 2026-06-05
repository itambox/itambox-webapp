import django_filters
from core.filters import BaseFilterSet
from django.db.models import Q
from django import forms
from organization.models import Tenant
from .models import Provider, Subscription, SubscriptionAssignment, SubscriptionStatusChoices, SubscriptionTypeChoices


class SubscriptionFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Name, Description, Contract...'})
    )
    type = django_filters.ChoiceFilter(
        field_name='type',
        choices=SubscriptionTypeChoices.choices,
        label='Type',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    status = django_filters.ChoiceFilter(
        field_name='status',
        choices=SubscriptionStatusChoices.choices,
        label='Status',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tenant = django_filters.ModelChoiceFilter(
        queryset=Tenant.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Tenant'
    )
    provider = django_filters.ModelChoiceFilter(
        queryset=Provider.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Provider'
    )
    renewal_within = django_filters.NumberFilter(
        method='filter_renewal_within',
        label='Renews Within (Days)',
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'e.g. 30'})
    )

    class Meta:
        model = Subscription
        fields = ['type', 'status', 'tenant', 'provider']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(description__icontains=value) |
            Q(notes__icontains=value) |
            Q(contract_reference__icontains=value) |
            Q(provider__name__icontains=value)
        ).distinct()

    def filter_renewal_within(self, queryset, name, value):
        if value:
            from django.utils import timezone
            cutoff = timezone.now().date() + timezone.timedelta(days=int(value))
            return queryset.filter(renewal_date__lte=cutoff, renewal_date__gte=timezone.now().date())
        return queryset


class ProviderFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Name, Account ID...'})
    )
    is_active = django_filters.BooleanFilter(
        method='filter_is_active',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        label='Active'
    )

    class Meta:
        model = Provider
        fields = []  # No auto-generated filters — all are custom

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(account_id__icontains=value) |
            Q(admin_notes__icontains=value)
        ).distinct()

    def filter_is_active(self, queryset, name, value):
        if value:  # Only filter when checkbox is explicitly checked
            return queryset.filter(is_active=True)
        return queryset  # Unchecked = show all


class SubscriptionAssignmentFilterSet(BaseFilterSet):
    class Meta:
        model = SubscriptionAssignment
        fields = ['subscription', 'content_type', 'object_id']
