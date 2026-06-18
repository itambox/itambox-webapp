import django_filters
from core.filters import BaseFilterSet
from django.db.models import Q
from django import forms
from django.utils.translation import gettext_lazy as _
from extras.models import Tag
from software.models import Software
from organization.models import Tenant, AssetHolder
from assets.models import Asset
from .models import License, LicenseTypeChoices, LicenseSeatAssignment


class LicenseFilterSet(BaseFilterSet):
    """FilterSet for querying License entitlements."""
    q = django_filters.CharFilter(
        method='search',
        label=_('Search'),
        widget=forms.TextInput(attrs={'placeholder': 'Name, Key, Order Number...'})
    )
    software = django_filters.ModelChoiceFilter(
        field_name='software',
        queryset=Software.objects.all(),
        label=_('Software Catalog'),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    license_type = django_filters.ChoiceFilter(
        field_name='license_type',
        choices=LicenseTypeChoices.choices,
        label=_('License Type'),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tags = django_filters.ModelMultipleChoiceFilter(
        field_name='tags',
        queryset=Tag.objects.all(),
        label=_('Tags'),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'})
    )
    tenant = django_filters.ModelChoiceFilter(
        queryset=Tenant.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_('Tenant')
    )

    class Meta:
        model = License
        fields = ['software', 'license_type', 'tags']

    def search(self, queryset, name, value):
        """Perform a comprehensive search across relevant fields."""
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(product_key__icontains=value) |
            Q(order_number__icontains=value) |
            Q(notes__icontains=value)
        ).distinct()


class LicenseSeatAssignmentFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(method='search', label=_('Search'))
    license = django_filters.ModelChoiceFilter(
        queryset=License.objects.all(),
        label=_('License'),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    asset = django_filters.ModelChoiceFilter(
        queryset=Asset.objects.all(),
        label=_('Asset'),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    assigned_holder = django_filters.ModelChoiceFilter(
        queryset=AssetHolder.objects.all(),
        label=_('Assigned Holder'),
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = LicenseSeatAssignment
        fields = ['license', 'asset', 'assigned_holder']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(notes__icontains=value) |
            Q(license__name__icontains=value) |
            Q(assigned_holder__first_name__icontains=value) |
            Q(assigned_holder__last_name__icontains=value) |
            Q(asset__name__icontains=value)
        ).distinct()

