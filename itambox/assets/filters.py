import django_filters
from core.filters import BaseFilterSet
from .models import Asset, AssetRole, Manufacturer, AssetType, StatusLabel, Depreciation, Supplier, Category, AssetRequest, AssetTagSequence
from organization.models import Location, Tenant, AssetHolder
from extras.models import Tag
from django import forms
from django.db.models import Q

class AssetFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Name, Tag, Serial...'})
    )

    status = django_filters.ModelChoiceFilter(
        queryset=StatusLabel.objects.all(),
        label='Status',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    asset_role = django_filters.ModelChoiceFilter(
        queryset=AssetRole.objects.all(),
        label='Asset Role',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    asset_type = django_filters.ModelChoiceFilter(
        queryset=AssetType.objects.all().select_related('manufacturer'),
        label='Asset Type',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    manufacturer = django_filters.ModelChoiceFilter(
        field_name='asset_type__manufacturer',
        queryset=Manufacturer.objects.all(),
        label='Manufacturer',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    category = django_filters.ModelChoiceFilter(
        field_name='asset_type__category',
        queryset=Category.objects.all(),
        label='Category',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    location = django_filters.ModelChoiceFilter(
        queryset=Location.objects.all().select_related('site'),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tenant = django_filters.ModelChoiceFilter(
        queryset=Tenant.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Tenant'
    )
    assigned_to = django_filters.ModelChoiceFilter(
        field_name='assignments__assigned_user',
        queryset=AssetHolder.objects.all(),
        label='Assigned To',
        widget=forms.Select(attrs={'class': 'form-select'}),
        method='filter_assigned_to'
    )
    supplier = django_filters.ModelChoiceFilter(
        queryset=Supplier.objects.all(),
        label='Supplier',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tags = django_filters.ModelMultipleChoiceFilter(
        field_name='tags__slug',
        queryset=Tag.objects.all(),
        to_field_name='slug',
        label='Tags',
        conjoined=True,
        widget=forms.SelectMultiple(attrs={'class': 'form-select'})
    )
    purchase_date_after = django_filters.DateFilter(
        field_name='purchase_date',
        lookup_expr='gte',
        label='Purchased After',
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    purchase_date_before = django_filters.DateFilter(
        field_name='purchase_date',
        lookup_expr='lte',
        label='Purchased Before',
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    requestable = django_filters.BooleanFilter(
        method='filter_requestable',
        label='Requestable',
        widget=forms.Select(choices=[('', 'Any'), ('true', 'Yes'), ('false', 'No')], attrs={'class': 'form-select'})
    )

    class Meta:
        model = Asset
        # All filters defined explicitly above — no auto-generated fields needed
        fields = []

    def filter_requestable(self, queryset, name, value):
        if value is None:
            return queryset
        if value:
            return queryset.filter(
                Q(requestable=True) | Q(requestable__isnull=True, asset_type__requestable=True)
            )
        else:
            return queryset.filter(
                Q(requestable=False) | Q(requestable__isnull=True, asset_type__isnull=True) | Q(requestable__isnull=True, asset_type__requestable=False)
            )

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

    def filter_assigned_to(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(assignments__assigned_user=value, assignments__is_active=True)

# --- AssetRole Filter --- 
class AssetRoleFilterSet(BaseFilterSet):
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
class ManufacturerFilterSet(BaseFilterSet):
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

class AssetTypeFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
    )
    manufacturer = django_filters.ModelChoiceFilter(
        queryset=Manufacturer.objects.all(),
        field_name='manufacturer',
        label='Manufacturer'
    )
    requestable = django_filters.BooleanFilter(
        label='Requestable',
        widget=forms.Select(choices=[('', 'Any'), ('true', 'Yes'), ('false', 'No')], attrs={'class': 'form-select'})
    )

    class Meta:
        model = AssetType
        fields = ['manufacturer', 'model', 'part_number', 'requestable']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(model__icontains=value) |
            Q(part_number__icontains=value) |
            Q(description__icontains=value) |
            Q(manufacturer__name__icontains=value)
        ).distinct()


class StatusLabelFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Name, Description...'})
    )
    type = django_filters.MultipleChoiceFilter(
        choices=StatusLabel.TYPE_CHOICES,
        widget=forms.SelectMultiple(attrs={'class': 'form-select'})
    )

    class Meta:
        model = StatusLabel
        fields = ['name', 'type']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(description__icontains=value)
        ).distinct()


class DepreciationFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(method='search', label='Search')

    class Meta:
        model = Depreciation
        fields = ['name', 'months']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value)
        ).distinct()
class SupplierFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(method='search', label='Search', widget=forms.TextInput(attrs={'placeholder': 'Name...'}))

    class Meta:
        model = Supplier
        fields = ['name']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) | Q(website__icontains=value) | Q(contact_name__icontains=value)
        ).distinct()


class CategoryFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(method='search', label='Search', widget=forms.TextInput(attrs={'placeholder': 'Name...'}))

    class Meta:
        model = Category
        fields = ['name']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) | Q(description__icontains=value)
        ).distinct()


class AssetRequestFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(method='search', label='Search', widget=forms.TextInput(attrs={'placeholder': 'Search...'}))
    status = django_filters.ChoiceFilter(choices=AssetRequest.STATUS_CHOICES, widget=forms.Select(attrs={'class': 'form-select'}))

    class Meta:
        model = AssetRequest
        fields = ['status']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(asset__name__icontains=value) |
            Q(asset_type__model__icontains=value) |
            Q(requester__username__icontains=value) |
            Q(notes__icontains=value)
        ).distinct()


class AssetTagSequenceFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Prefix...'})
    )
    tenant = django_filters.ModelChoiceFilter(
        queryset=Tenant.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Tenant'
    )
    category = django_filters.ModelChoiceFilter(
        queryset=Category.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Category'
    )
    is_active = django_filters.BooleanFilter(
        widget=forms.Select(choices=[('', 'All'), ('True', 'Active'), ('False', 'Inactive')], attrs={'class': 'form-select'}),
        label='Active'
    )

    class Meta:
        model = AssetTagSequence
        fields = ['prefix', 'tenant', 'category', 'is_active']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(prefix__icontains=value)
        ).distinct()


# AuditSessionFilterSet / AssetAuditFilterSet moved to compliance.filters