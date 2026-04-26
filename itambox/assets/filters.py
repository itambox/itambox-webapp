import django_filters
from .models import Asset, AssetRole, Manufacturer, AssetType, StatusLabel, Depreciation, Supplier, Category, AssetRequest, AssetTagSequence, AuditSession, AssetAudit
from organization.models import Location, Tenant
from extras.models import Tag
from django import forms
from django.db.models import Q

class AssetFilterSet(django_filters.FilterSet):
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
    location = django_filters.ModelChoiceFilter(
        queryset=Location.objects.all().select_related('site'),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tenant = django_filters.ModelChoiceFilter(
        queryset=Tenant.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Tenant'
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
        label='Requestable',
        widget=forms.Select(choices=[('', 'Any'), ('true', 'Yes'), ('false', 'No')], attrs={'class': 'form-select'})
    )

    class Meta:
        model = Asset
        # All filters defined explicitly above — no auto-generated fields needed
        fields = []

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


class StatusLabelFilterSet(django_filters.FilterSet):
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


class DepreciationFilterSet(django_filters.FilterSet):
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
class SupplierFilterSet(django_filters.FilterSet):
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


class CategoryFilterSet(django_filters.FilterSet):
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


class AssetRequestFilterSet(django_filters.FilterSet):
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


class AssetTagSequenceFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
        widget=forms.TextInput(attrs={'placeholder': 'Prefix...'})
    )

    class Meta:
        model = AssetTagSequence
        fields = ['prefix']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(prefix__icontains=value)
        ).distinct()


class AuditSessionFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(method='search', label='Search', widget=forms.TextInput(attrs={'placeholder': 'Name...'}))
    status = django_filters.ChoiceFilter(
        choices=[
            ('planned', 'Planned'),
            ('active', 'Active'),
            ('completed', 'Completed'),
        ],
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    location = django_filters.ModelChoiceFilter(
        queryset=Location.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Location'
    )

    class Meta:
        model = AuditSession
        fields = ['status', 'location']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value)
        ).distinct()


class AssetAuditFilterSet(django_filters.FilterSet):
    q = django_filters.CharFilter(method='search', label='Search', widget=forms.TextInput(attrs={'placeholder': 'Notes...'}))
    session = django_filters.ModelChoiceFilter(
        queryset=AuditSession.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Audit Session'
    )
    asset = django_filters.ModelChoiceFilter(
        queryset=Asset.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Asset'
    )
    location = django_filters.ModelChoiceFilter(
        queryset=Location.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Observed Location'
    )
    status = django_filters.ModelChoiceFilter(
        queryset=StatusLabel.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Observed Status'
    )

    class Meta:
        model = AssetAudit
        fields = ['session', 'asset', 'location', 'status', 'verification_method']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(notes__icontains=value) |
            Q(asset__name__icontains=value) |
            Q(auditor__username__icontains=value)
        ).distinct()