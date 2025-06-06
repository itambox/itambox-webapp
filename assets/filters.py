import django_filters
from .models import Asset, Manufacturer, AssetRole # Updated import
from assetbox.organization.models import Location # Absolute import
from django.db.models import Q

# Renamed from CategoryFilterSet
class AssetRoleFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
    )
    class Meta:
        model = AssetRole # Updated model
        fields = ['name', 'slug'] # Add more fields if needed

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        # Search name and description
        return queryset.filter(
            Q(name__icontains=value) |
            Q(description__icontains=value)
        ).distinct()

class ManufacturerFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
    )
    class Meta:
        model = Manufacturer
        fields = ['name', 'slug']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(description__icontains=value)
        ).distinct()

class AssetFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
    )
    asset_role = django_filters.ModelMultipleChoiceFilter( # Updated field name
        queryset=AssetRole.objects.all(), # Updated model
        field_name='asset_role__slug', # Updated field name
        to_field_name='slug',
        label='Asset Role (Slug)', # Updated label
    )
    manufacturer = django_filters.ModelMultipleChoiceFilter(
        queryset=Manufacturer.objects.all(),
        field_name='manufacturer__slug',
        to_field_name='slug',
        label='Manufacturer (Slug)',
    )
    location = django_filters.ModelMultipleChoiceFilter(
        queryset=Location.objects.all(), # Use Location model
        field_name='location__slug',
        to_field_name='slug',
        label='Location (Slug)',
    )

    class Meta:
        model = Asset
        fields = ['status', 'asset_role', 'manufacturer', 'location'] # Updated field

    def search(self, queryset, name, value):
        # Simple search across name, asset tag, serial number
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(asset_tag__icontains=value) |
            Q(serial_number__icontains=value)
        ).distinct() 