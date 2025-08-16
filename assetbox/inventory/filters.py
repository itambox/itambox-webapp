import django_filters
from django import forms
from django.db.models import Q

from organization.models import Tenant
from assets.models import Manufacturer
from .models import Accessory, Consumable, Kit


class AccessoryFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Name, Part Number...'})
    )
    manufacturer = django_filters.ModelChoiceFilter(
        queryset=Manufacturer.objects.all(),
        label='Manufacturer',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tenant = django_filters.ModelChoiceFilter(
        queryset=Tenant.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Tenant'
    )

    class Meta:
        model = Accessory
        fields = []  # All defined explicitly above

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(part_number__icontains=value) |
            Q(notes__icontains=value)
        ).distinct()


class ConsumableFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Name, Part Number...'})
    )
    manufacturer = django_filters.ModelChoiceFilter(
        queryset=Manufacturer.objects.all(),
        label='Manufacturer',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tenant = django_filters.ModelChoiceFilter(
        queryset=Tenant.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Tenant'
    )

    class Meta:
        model = Consumable
        fields = ['manufacturer', 'category']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(part_number__icontains=value) |
            Q(notes__icontains=value)
        ).distinct()


class KitFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(method='search', label='Search')
    tenant = django_filters.ModelChoiceFilter(
        queryset=Tenant.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Tenant'
    )

    class Meta:
        model = Kit
        fields = ['name']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(description__icontains=value)
        ).distinct()
