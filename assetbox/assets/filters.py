import django_filters
from .models import Asset, AssetRole, Manufacturer
from organization.models import Location
from django import forms
from django.db.models import Q
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Initialize FormHelper
        self.form.helper = FormHelper()
        self.form.helper.form_method = 'get' # Important for filters
        self.form.helper.form_tag = False # Template handles <form> tag
        # Define a simple layout, adding the submit button
        self.form.helper.layout = Layout(
            *self.filters.keys(), # Render all defined filter fields
            HTML('<div class="mt-3">'),
            Submit('submit', 'Apply Filter', css_class='btn btn-primary'),
            HTML('<a href="{{ request.path }}" class="btn btn-secondary ms-2">Clear Filters</a>'),
            HTML('</div>')
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.form.helper = FormHelper()
        self.form.helper.form_method = 'get'
        self.form.helper.form_tag = False
        self.form.helper.layout = Layout(
            *self.filters.keys(),
            HTML('<div class="mt-3">'),
            Submit('submit', 'Apply Filter', css_class='btn btn-primary'),
            HTML('<a href="{{ request.path }}" class="btn btn-secondary ms-2">Clear Filters</a>'),
            HTML('</div>')
        )

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.form.helper = FormHelper()
        self.form.helper.form_method = 'get'
        self.form.helper.form_tag = False
        self.form.helper.layout = Layout(
            *self.filters.keys(),
            HTML('<div class="mt-3">'),
            Submit('submit', 'Apply Filter', css_class='btn btn-primary'),
            HTML('<a href="{{ request.path }}" class="btn btn-secondary ms-2">Clear Filters</a>'),
            HTML('</div>')
        )

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(description__icontains=value)
        ).distinct() 