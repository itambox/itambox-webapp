import django_filters
from django import forms
from django.db.models import Q
from assets.models import Asset
from .models import AssetMaintenance

class AssetMaintenanceFilterSet(django_filters.FilterSet):
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
