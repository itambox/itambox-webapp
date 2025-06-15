import django_filters
from .models import Asset, AssetRole, Manufacturer, AssetType
from organization.models import Location
from django import forms
from django.db.models import Q
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML
from django.contrib.contenttypes.models import ContentType

class AssetFilterSet(django_filters.FilterSet):
    # Add filters for specific fields
    # Q filter for searching across multiple fields (like NetBox)
    q = django_filters.CharFilter(
        method='search',
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Name, Tag, Serial...'})
    )

    status = django_filters.MultipleChoiceFilter(
        choices=Asset.STATUS_CHOICES,
        widget=forms.SelectMultiple(attrs={'class': 'form-select'})
    )
    asset_role = django_filters.ModelChoiceFilter(
        queryset=AssetRole.objects.all(),
        label='Asset Role',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    manufacturer = django_filters.ModelChoiceFilter(
        queryset=Manufacturer.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    location = django_filters.ModelChoiceFilter(
        queryset=Location.objects.all().select_related('site'), # Optimize choices
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = Asset
        # Define fields that can be filtered directly (in addition to custom ones above)
        fields = ['status', 'asset_role', 'manufacturer', 'location'] # Keep this minimal if defining explicitly

    def search(self, queryset, name, value):
        """Perform basic search across designated fields."""
        if not value.strip():
            return queryset
        # Basic search across name, asset_tag, serial_number (can be expanded)
        # Consider adding asset_holder.name if performance allows
        return queryset.filter(
            Q(name__icontains=value) |
            Q(asset_tag__icontains=value) |
            Q(serial_number__icontains=value)
        ).distinct()

# --- AssetRole Filter --- 
class AssetRoleFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Name, Description...'})
    )

    class Meta:
        model = AssetRole
        fields = ['name'] # Add other specific fields if needed later

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(description__icontains=value)
        ).distinct()

# --- Manufacturer Filter --- 
class ManufacturerFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Name, Description...'})
    )

    class Meta:
        model = Manufacturer
        fields = ['name'] # Add other specific fields if needed later

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(description__icontains=value)
        ).distinct()

class AssetTypeFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
    )
    manufacturer = django_filters.ModelChoiceFilter(
        queryset=Manufacturer.objects.all(),
        field_name='manufacturer',
        label='Manufacturer'
    )

    class Meta:
        model = AssetType
        fields = ['manufacturer', 'model', 'part_number', 'cpu', 'storage_type']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(model__icontains=value) |
            Q(part_number__icontains=value) |
            Q(description__icontains=value) |
            Q(cpu__icontains=value) |
            Q(gpu__icontains=value) |
            Q(manufacturer__name__icontains=value)
        ).distinct() 