import django_filters
from core.filters import BaseFilterSet
from django import forms
from django.db.models import Q

from organization.models import Tenant, Location, AssetHolder
from assets.models import Manufacturer, AssetType, Category, Asset
from licenses.models import License
from .models import (
    Accessory, Consumable, Kit, AccessoryStock, ConsumableStock,
    AccessoryAssignment, ConsumableAssignment, KitItem,
    Component, ComponentStock, ComponentAllocation
)



class AccessoryFilterSet(BaseFilterSet):
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


class ConsumableFilterSet(BaseFilterSet):
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


class KitFilterSet(BaseFilterSet):
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


class AccessoryStockFilterSet(BaseFilterSet):
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


class ConsumableStockFilterSet(BaseFilterSet):
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


class AccessoryAssignmentFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(method='search', label='Search')
    accessory = django_filters.ModelChoiceFilter(
        queryset=Accessory.objects.all(),
        label='Accessory',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    assigned_holder = django_filters.ModelChoiceFilter(
        queryset=AssetHolder.objects.all(),
        label='Assigned Holder',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    assigned_location = django_filters.ModelChoiceFilter(
        queryset=Location.objects.all(),
        label='Assigned Location',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    from_location = django_filters.ModelChoiceFilter(
        queryset=Location.objects.all(),
        label='From Location',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = AccessoryAssignment
        fields = ['accessory', 'assigned_holder', 'assigned_location', 'from_location']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(notes__icontains=value) |
            Q(accessory__name__icontains=value) |
            Q(assigned_holder__first_name__icontains=value) |
            Q(assigned_holder__last_name__icontains=value)
        ).distinct()


class ConsumableAssignmentFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(method='search', label='Search')
    consumable = django_filters.ModelChoiceFilter(
        queryset=Consumable.objects.all(),
        label='Consumable',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    assigned_holder = django_filters.ModelChoiceFilter(
        queryset=AssetHolder.objects.all(),
        label='Assigned Holder',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    assigned_location = django_filters.ModelChoiceFilter(
        queryset=Location.objects.all(),
        label='Assigned Location',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    from_location = django_filters.ModelChoiceFilter(
        queryset=Location.objects.all(),
        label='From Location',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = ConsumableAssignment
        fields = ['consumable', 'assigned_holder', 'assigned_location', 'from_location']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(notes__icontains=value) |
            Q(consumable__name__icontains=value) |
            Q(assigned_holder__first_name__icontains=value) |
            Q(assigned_holder__last_name__icontains=value)
        ).distinct()


class KitItemFilterSet(BaseFilterSet):
    kit = django_filters.ModelChoiceFilter(
        queryset=Kit.objects.all(),
        label='Kit',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    asset_type = django_filters.ModelChoiceFilter(
        queryset=AssetType.objects.all(),
        label='Asset Type',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    accessory = django_filters.ModelChoiceFilter(
        queryset=Accessory.objects.all(),
        label='Accessory',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    license = django_filters.ModelChoiceFilter(
        queryset=License.objects.all(),
        label='License',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    consumable = django_filters.ModelChoiceFilter(
        queryset=Consumable.objects.all(),
        label='Consumable',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = KitItem
        fields = ['kit', 'asset_type', 'accessory', 'license', 'consumable']


class ComponentFilterSet(BaseFilterSet):
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
            Q(notes__icontains=value)
        ).distinct()


class ComponentStockFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Component or Location...'})
    )
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

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(component__name__icontains=value) |
            Q(location__name__icontains=value)
        ).distinct()


class ComponentAllocationFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(method='search', label='Search')
    component = django_filters.ModelChoiceFilter(
        queryset=Component.objects.all(),
        label='Component',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    assigned_holder = django_filters.ModelChoiceFilter(
        queryset=AssetHolder.objects.all(),
        label='Assigned Holder',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    assigned_location = django_filters.ModelChoiceFilter(
        queryset=Location.objects.all(),
        label='Assigned Location',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    assigned_asset = django_filters.ModelChoiceFilter(
        queryset=Asset.objects.all(),
        label='Assigned Asset',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    from_location = django_filters.ModelChoiceFilter(
        queryset=Location.objects.all(),
        label='From Location',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = ComponentAllocation
        fields = ['component', 'assigned_holder', 'assigned_location', 'assigned_asset', 'from_location']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(notes__icontains=value) |
            Q(component__name__icontains=value) |
            Q(assigned_holder__first_name__icontains=value) |
            Q(assigned_holder__last_name__icontains=value)
        ).distinct()


