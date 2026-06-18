import django_filters
from core.filters import BaseFilterSet
from django.db.models import Q
from django import forms
from django.utils.translation import gettext_lazy as _
from extras.filters import TagFilter # Assuming TagFilter exists for M2M
from assets.models import Manufacturer
from extras.models import Tag
from .models import Software

class SoftwareFilterSet(BaseFilterSet):
    """FilterSet for querying Software instances."""
    q = django_filters.CharFilter(
        method='search',
        label=_('Search'),
        widget=forms.TextInput(attrs={'placeholder': 'Name, Description...'})
    )
    manufacturer = django_filters.ModelChoiceFilter(
        field_name='manufacturer',
        queryset=Manufacturer.objects.all(),
        label=_('Manufacturer'),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tags = django_filters.ModelMultipleChoiceFilter(
        field_name='tags',
        queryset=Tag.objects.all(),
        label=_('Tags'),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'})
    )

    class Meta:
        model = Software
        fields = ['manufacturer', 'tags']

    def search(self, queryset, name, value):
        """Perform a comprehensive search across relevant fields."""
        if not value.strip():
            return queryset
        # Adjust fields based on what's searchable
        return queryset.filter(
            Q(name__icontains=value) |
            Q(description__icontains=value) |
            Q(manufacturer__name__icontains=value) # Search manufacturer name
        ).distinct() 