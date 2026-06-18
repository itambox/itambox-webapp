import django_filters
from core.filters import BaseFilterSet
from django import forms
from django.db.models import Q
from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext_lazy as _
from assets.models import Manufacturer, AssetType
from organization.models import Site, Region, SiteGroup, Location, Tenant, TenantGroup, AssetHolder, Contact, ContactRole, ContactAssignment, TenantRole, TenantMembership, CostCenter

from extras.models import Tag # Import Tag
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML # Import Helper, Layout, Submit

# --- Base Search Filter --- 
class BaseOrgFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(
        method='search',
        label=_('Search'),
        widget=forms.TextInput(attrs={'placeholder': 'Search...'})
    )
    # Common tag filter
    tag = django_filters.ModelMultipleChoiceFilter(
        field_name='tags__slug',
        queryset=Tag.objects.all(),
        to_field_name='slug',
        label=_('Tags'),
        conjoined=True, # Use AND logic for tags
        widget=forms.SelectMultiple(attrs={'class': 'form-select'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Initialize FormHelper
        self.form.helper = FormHelper()
        self.form.helper.form_method = 'get' # Important for filters
        self.form.helper.form_tag = False # Template handles <form> tag
        # Define a simple layout, adding the submit button
        # We'll add fields dynamically based on the FilterSet definition
        self.form.helper.layout = Layout(
            *self.filters.keys(), # Render all defined filter fields
            HTML('<div class="mt-3">'), # Add margin like the template had
            Submit('submit', 'Apply Filter', css_class='btn btn-primary'),
            # Add Clear button as HTML link within the layout
            HTML('<a href="{{ request.path }}" class="btn btn-secondary ms-2">Clear Filters</a>'),
            HTML('</div>')
        )

    def search(self, queryset, name, value):
        # Default search: name and description (override in subclasses)
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(description__icontains=value)
        ).distinct()

# --- Region Filter --- 
class RegionFilterSet(BaseOrgFilterSet):
    parent = django_filters.ModelChoiceFilter(
        queryset=Region.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    class Meta:
        model = Region
        fields = ['name', 'parent']

# --- SiteGroup Filter ---
class SiteGroupFilterSet(BaseOrgFilterSet):
    parent = django_filters.ModelChoiceFilter(
        queryset=SiteGroup.objects.all(),
        label=_("Parent Group"),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    class Meta:
        model = SiteGroup
        fields = ['name', 'parent']

# --- Site Filter --- 
class SiteFilterSet(BaseOrgFilterSet):
    status = django_filters.MultipleChoiceFilter(
        choices=Site.STATUS_CHOICES,
        widget=forms.SelectMultiple(attrs={'class': 'form-select'})
    )
    region = django_filters.ModelChoiceFilter(
        queryset=Region.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    group = django_filters.ModelChoiceFilter(
        queryset=SiteGroup.objects.all(),
        label=_("Site Group"),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tenant = django_filters.ModelChoiceFilter(
        queryset=Tenant.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    class Meta:
        model = Site
        fields = ['name', 'status', 'region', 'group', 'tenant']

    # Override default search for Site
    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(description__icontains=value) |
            Q(facility__icontains=value) | # Include facility
            Q(physical_address__icontains=value) |
            Q(comments__icontains=value)
        ).distinct()

# --- Location Filter --- 
class LocationFilterSet(BaseOrgFilterSet):
    status = django_filters.MultipleChoiceFilter(
        choices=Location.STATUS_CHOICES,
        widget=forms.SelectMultiple(attrs={'class': 'form-select'})
    )
    site = django_filters.ModelChoiceFilter(
        queryset=Site.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    parent = django_filters.ModelChoiceFilter(
        queryset=Location.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tenant = django_filters.ModelChoiceFilter(
        queryset=Tenant.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    class Meta:
        model = Location
        fields = ['name', 'status', 'site', 'parent', 'tenant']

    # Override default search for Location
    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(description__icontains=value) |
            Q(facility__icontains=value)
        ).distinct()

# --- TenantGroup Filter ---
class TenantGroupFilterSet(BaseOrgFilterSet):
    parent = django_filters.ModelChoiceFilter(
        queryset=TenantGroup.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    class Meta:
        model = TenantGroup
        fields = ['name', 'parent']

# --- Tenant Filter --- 
class TenantFilterSet(BaseOrgFilterSet):
    group = django_filters.ModelChoiceFilter(
        queryset=TenantGroup.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    class Meta:
        model = Tenant
        fields = ['name', 'group']

    # Override default search for Tenant
    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(description__icontains=value) |
            Q(comments__icontains=value)
        ).distinct()

# --- AssetHolder Filter ---
class AssetHolderFilterSet(BaseOrgFilterSet):
    tenant = django_filters.ModelChoiceFilter(
        queryset=Tenant.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    class Meta:
        model = AssetHolder
        fields = ['first_name', 'last_name', 'upn', 'email', 'tenant']

    # Override default search for AssetHolder
    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(first_name__icontains=value) |
            Q(last_name__icontains=value) |
            Q(upn__icontains=value) |
            Q(email__icontains=value) |
            Q(description__icontains=value) |
            Q(comments__icontains=value)
        ).distinct() 
    
class AssetTypeFilterSet(BaseOrgFilterSet):
    manufacturer = django_filters.ModelChoiceFilter(
        queryset=Manufacturer.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    class Meta:
        model = AssetType
        fields = ['manufacturer', 'model', 'part_number']
    
    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(model__icontains=value) |
            Q(part_number__icontains=value) |
            Q(description__icontains=value) |
            Q(manufacturer__name__icontains=value)
        ).distinct()


# --- Contact Filter ---
class ContactFilterSet(BaseOrgFilterSet):
    class Meta:
        model = Contact
        fields = ['name', 'title', 'phone', 'email']

    # Override default search for Contact
    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(title__icontains=value) |
            Q(phone__icontains=value) |
            Q(email__icontains=value) |
            Q(description__icontains=value) |
            Q(comments__icontains=value)
        ).distinct()


# --- ContactRole Filter ---
class ContactRoleFilterSet(BaseOrgFilterSet):
    tag = None  # ContactRole has no tags field — disable inherited filter

    class Meta:
        model = ContactRole
        fields = ['name']

    # Override default search for ContactRole
    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(slug__icontains=value) |
            Q(description__icontains=value)
        ).distinct()




class TenantRoleFilterSet(BaseOrgFilterSet):
    tag = None  # TenantRole has no tags field
    tenant = django_filters.ModelChoiceFilter(
        queryset=Tenant.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = TenantRole
        fields = ['name', 'tenant']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(description__icontains=value)
        ).distinct()


def _user_queryset(request):
    from django.contrib.auth import get_user_model
    return get_user_model().objects.all()


class TenantMembershipFilterSet(BaseOrgFilterSet):
    tag = None  # TenantMembership has no tags field
    tenant = django_filters.ModelChoiceFilter(
        queryset=Tenant.objects.all(),
        label=_("Tenant"),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    role = django_filters.ModelChoiceFilter(
        queryset=TenantRole.objects.all(),
        label=_("Role"),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    user = django_filters.ModelChoiceFilter(
        queryset=_user_queryset,
        label=_("User"),
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = TenantMembership
        fields = ['tenant', 'role', 'user']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(user__username__icontains=value) |
            Q(user__email__icontains=value) |
            Q(role__name__icontains=value) |
            Q(tenant__name__icontains=value)
        ).distinct()


class CostCenterFilterSet(BaseOrgFilterSet):
    tag = None  # CostCenter has no tags field

    tenant = django_filters.ModelChoiceFilter(
        queryset=Tenant.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    parent = django_filters.ModelChoiceFilter(
        queryset=CostCenter.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    is_active = django_filters.BooleanFilter(
        widget=forms.Select(
            choices=[('', _('Any')), ('true', _('Yes')), ('false', _('No'))],
            attrs={'class': 'form-select'},
        ),
    )

    class Meta:
        model = CostCenter
        fields = ['name', 'code', 'tenant', 'parent', 'is_active']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) |
            Q(code__icontains=value) |
            Q(description__icontains=value)
        ).distinct()


class ContactAssignmentFilterSet(BaseOrgFilterSet):
    tag = None  # ContactAssignment has no tags field
    contact = django_filters.ModelChoiceFilter(
        queryset=Contact.objects.all(),
        label=_("Contact"),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    role = django_filters.ModelChoiceFilter(
        queryset=ContactRole.objects.all(),
        label=_("Role"),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    content_type = django_filters.ModelChoiceFilter(
        queryset=ContentType.objects.all(),
        label=_("Object Type"),
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = ContactAssignment
        fields = ['contact', 'role', 'content_type', 'object_id']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(contact__name__icontains=value) |
            Q(contact__email__icontains=value) |
            Q(role__name__icontains=value)
        ).distinct()



    