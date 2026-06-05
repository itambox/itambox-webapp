import django_filters
from core.filters import BaseFilterSet
from django import forms
from django.db.models import Q
from assets.models import Asset
from organization.models import AssetHolder
from .models import AssetMaintenance, CustodyReceipt

class CustodyReceiptFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Asset, Holder, Token...'})
    )
    asset = django_filters.ModelChoiceFilter(
        queryset=Asset.objects.all(),
        label='Asset',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    holder = django_filters.ModelChoiceFilter(
        queryset=AssetHolder.objects.all(),
        label='Holder',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    acceptance_status = django_filters.ChoiceFilter(
        choices=CustodyReceipt.ACCEPTANCE_STATUS_CHOICES,
        label='Acceptance Status',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    accepted = django_filters.BooleanFilter(
        label='Accepted',
        widget=forms.Select(choices=[('', 'Any'), ('true', 'Yes'), ('false', 'No')], attrs={'class': 'form-select'})
    )

    class Meta:
        model = CustodyReceipt
        fields = ['asset', 'holder', 'acceptance_status', 'accepted']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(token__icontains=value) |
            Q(asset__name__icontains=value) |
            Q(holder__first_name__icontains=value) |
            Q(holder__last_name__icontains=value) |
            Q(holder__upn__icontains=value) |
            Q(holder__email__icontains=value)
        ).distinct()


class AssetMaintenanceFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Supplier, Notes, Asset Name...'})
    )
    asset = django_filters.ModelChoiceFilter(
        queryset=Asset.objects.all(),
        label='Asset',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    maintenance_type = django_filters.ChoiceFilter(
        choices=AssetMaintenance.MAINTENANCE_TYPE_CHOICES,
        label='Type',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = AssetMaintenance
        fields = ['asset', 'maintenance_type', 'supplier']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(supplier__icontains=value) |
            Q(notes__icontains=value) |
            Q(asset__name__icontains=value)
        ).distinct()

