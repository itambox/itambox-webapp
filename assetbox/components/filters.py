import django_filters
from django import forms
from django.db.models import Q
from assets.models import Manufacturer
from .models import ComponentType, ComponentInstance

class ComponentTypeFilterSet(django_filters.FilterSet):
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

    class Meta:
        model = ComponentType
        fields = ['manufacturer', 'category']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(part_number__icontains=value) |
            Q(description__icontains=value) |
            Q(specs__icontains=value)
        ).distinct()


class ComponentInstanceFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Serial, Notes...'})
    )
    component_type = django_filters.ModelChoiceFilter(
        queryset=ComponentType.objects.all(),
        label='Component Model',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = ComponentInstance
        fields = ['component_type', 'status']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(serial_number__icontains=value) |
            Q(notes__icontains=value)
        ).distinct()
