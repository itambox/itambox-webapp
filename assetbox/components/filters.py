import django_filters
from django import forms
from django.db.models import Q
from assets.models import Manufacturer, Category, Asset
from organization.models import Location, Tenant
from .models import Component, ComponentStock, ComponentAllocation

class ComponentFilterSet(django_filters.FilterSet):
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
    category = django_filters.ModelChoiceFilter(
        queryset=Category.objects.filter(applies_to__component=True),
        label='Category',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tenant = django_filters.ModelChoiceFilter(
        queryset=Tenant.objects.all(),
        label='Tenant',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = Component
        fields = ['manufacturer', 'category', 'tenant']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(part_number__icontains=value) |
            Q(description__icontains=value)
        ).distinct()


class ComponentStockFilterSet(django_filters.FilterSet):
    component = django_filters.ModelChoiceFilter(
        queryset=Component.objects.all(),
        label='Component',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    location = django_filters.ModelChoiceFilter(
        queryset=Location.objects.all().select_related('site'),
        label='Location',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = ComponentStock
        fields = ['component', 'location']


class ComponentAllocationFilterSet(django_filters.FilterSet):
    component = django_filters.ModelChoiceFilter(
        queryset=Component.objects.all(),
        label='Component',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    asset = django_filters.ModelChoiceFilter(
        queryset=Asset.objects.all(),
        label='Asset',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = ComponentAllocation
        fields = ['component', 'asset']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(notes__icontains=value)
        ).distinct()
