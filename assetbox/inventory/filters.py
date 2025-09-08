import django_filters
from django import forms
from django.db.models import Q

from organization.models import Tenant, Location
from assets.models import Manufacturer
from .models import Accessory, Consumable, Kit, AccessoryStock, ConsumableStock


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


class AccessoryStockFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Accessory or Location...'})
    )
    accessory = django_filters.ModelChoiceFilter(
        queryset=Accessory.objects.all(),
        label='Accessory',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    location = django_filters.ModelChoiceFilter(
        queryset=Location.objects.all(),
        label='Location',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = AccessoryStock
        fields = ['accessory', 'location']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(accessory__name__icontains=value) |
            Q(location__name__icontains=value)
        ).distinct()


class ConsumableStockFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Consumable or Location...'})
    )
    consumable = django_filters.ModelChoiceFilter(
        queryset=Consumable.objects.all(),
        label='Consumable',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    location = django_filters.ModelChoiceFilter(
        queryset=Location.objects.all(),
        label='Location',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = ConsumableStock
        fields = ['consumable', 'location']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(consumable__name__icontains=value) |
            Q(location__name__icontains=value)
        ).distinct()

