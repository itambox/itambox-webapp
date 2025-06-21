import django_filters
from django.db.models import Q
from django import forms
from extras.models import Tag
from software.models import Software
from .models import License, LicenseTypeChoices

class LicenseFilterSet(django_filters.FilterSet):
    """FilterSet for querying License entitlements."""
    q = django_filters.CharFilter(
        method='search',
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Name, Key, Order Number...'})
    )
    software = django_filters.ModelChoiceFilter(
        field_name='software',
        queryset=Software.objects.all(),
        label='Software Catalog',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    license_type = django_filters.ChoiceFilter(
        field_name='license_type',
        choices=LicenseTypeChoices.choices,
        label='License Type',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tags = django_filters.ModelMultipleChoiceFilter(
        field_name='tags',
        queryset=Tag.objects.all(),
        label='Tags',
        widget=forms.SelectMultiple(attrs={'class': 'form-select'})
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
