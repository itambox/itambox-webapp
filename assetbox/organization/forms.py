from django import forms
# Import models from this app
from .models import Site, Region, SiteGroup, Tenant, Location, TenantGroup, AssetHolder, Contact, ContactRole, ContactAssignment
# Import models from other apps
from extras.models import Tag # UPDATED: Import Tag from extras
from django.contrib.auth import get_user_model
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Row, Column
from django.urls import reverse
from core.forms import FilterForm # Import the base FilterForm
from .filters import ( # Import the FilterSet classes
    SiteFilterSet, RegionFilterSet, SiteGroupFilterSet, LocationFilterSet,
    TenantFilterSet, TenantGroupFilterSet, AssetHolderFilterSet, ContactFilterSet, ContactRoleFilterSet
)


User = get_user_model()

# --- Standard Button Layout Helper --- 
def add_standard_buttons(helper, instance, list_url_name):
    """Adds standard Create/Update and Cancel buttons to a FormHelper."""
    button_text = 'Update' if instance and instance.pk else 'Create'
    cancel_url = reverse(list_url_name)
    helper.layout.append(
        HTML('<div class="mt-4"></div>')
    )
    helper.layout.append(
        Submit('submit', button_text, css_class='btn btn-primary')
    )
    helper.layout.append(
        HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>')
    )

# --- Site Form ---
class SiteForm(forms.ModelForm):
    # Point querysets to models within this app
    region = forms.ModelChoiceField(
        queryset=Region.objects.all(), 
        required=False, 
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    group = forms.ModelChoiceField(
        queryset=SiteGroup.objects.all(), 
        required=False, 
        label="Site Group",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.all(), 
        required=False, 
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}), # Or CheckboxSelectMultiple
    )

    class Meta:
        model = Site
        fields = [
            'name', 'slug', 'status', 'region', 'group', 'tenant', 
            'facility', 'time_zone', 'description', 'physical_address', 
            'shipping_address', 'latitude', 'longitude', 'comments', 'tags'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'facility': forms.TextInput(attrs={'class': 'form-control'}),
            'time_zone': forms.TextInput(attrs={'class': 'form-control'}), # Consider TimeZoneField later
            'description': forms.TextInput(attrs={'class': 'form-control'}),
            'physical_address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'shipping_address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'latitude': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001'}),
            'longitude': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.000001'}),
            'comments': forms.Textarea(attrs={'class': 'form-control', 'rows': 5}),
        }
        help_texts = {
            'slug': 'URL-friendly identifier.',
            'latitude': 'GPS coordinate (decimal format xx.yyyyyy)',
            'longitude': 'GPS coordinate (decimal format xx.yyyyyy)',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            # Define field layout using standard Crispy tags or list fields
            'name', 'slug', 'status',
            Row(
                Column('region', css_class='form-group col-md-4 mb-0'),
                Column('group', css_class='form-group col-md-4 mb-0'),
                Column('tenant', css_class='form-group col-md-4 mb-0'),
                css_class='mb-3'
            ),
            'facility', 'time_zone', 'description', 
            'physical_address', 'shipping_address',
            Row(
                Column('latitude', css_class='form-group col-md-6 mb-0'),
                Column('longitude', css_class='form-group col-md-6 mb-0'),
                css_class='mb-3'
            ),
            'comments', 'tags'
        )
        add_standard_buttons(self.helper, self.instance, 'organization:site_list')

# --- Region Form ---
class RegionForm(forms.ModelForm):
    parent = forms.ModelChoiceField(
        queryset=Region.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = Region
        fields = ['name', 'slug', 'parent', 'description', 'tags']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }
        help_texts = {
            'slug': 'URL-friendly identifier.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'name', 'slug', 'parent', 'description', 'tags'
        )
        add_standard_buttons(self.helper, self.instance, 'organization:region_list')

    def clean_parent(self):
        parent = self.cleaned_data.get('parent')
        if parent and self.instance and self.instance.pk and parent.pk == self.instance.pk:
            raise forms.ValidationError("A region cannot be its own parent.")
        return parent

# --- Site Group Form ---
class SiteGroupForm(forms.ModelForm):
    parent = forms.ModelChoiceField(
        queryset=SiteGroup.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = SiteGroup
        fields = ['name', 'slug', 'parent', 'description', 'tags']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }
        help_texts = {
            'slug': 'URL-friendly identifier.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'name', 'slug', 'parent', 'description', 'tags'
        )
        add_standard_buttons(self.helper, self.instance, 'organization:sitegroup_list')

    def clean_parent(self):
        parent = self.cleaned_data.get('parent')
        if parent and self.instance and self.instance.pk and parent.pk == self.instance.pk:
            raise forms.ValidationError("A site group cannot be its own parent.")
        return parent

# --- Location Form ---
class LocationForm(forms.ModelForm):
    site = forms.ModelChoiceField(
        queryset=Site.objects.all(),
        required=True, # Site is required
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    parent = forms.ModelChoiceField(
        queryset=Location.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = Location
        fields = [
            'site', 'name', 'slug', 'status', 'parent', 'tenant',
            'facility', 'description', 'tags'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'facility': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }
        help_texts = {
            'slug': 'URL-friendly identifier.',
            'facility': 'Building, Floor, Room, Rack, etc.'
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'site', 'name', 'slug', 'status', 'parent', 'tenant',
            'facility', 'description', 'tags'
        )
        add_standard_buttons(self.helper, self.instance, 'organization:location_list')

    def clean_parent(self):
        parent = self.cleaned_data.get('parent')
        if parent and self.instance and self.instance.pk:
            if parent.pk == self.instance.pk:
                raise forms.ValidationError("A location cannot be its own parent.")
        return parent

# --- TenantGroup Form ---
class TenantGroupForm(forms.ModelForm):
    parent = forms.ModelChoiceField(
        queryset=TenantGroup.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = TenantGroup
        fields = ['name', 'slug', 'parent', 'description', 'tags']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }
        help_texts = {
            'slug': 'URL-friendly identifier.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'name', 'slug', 'parent', 'description', 'tags'
        )
        add_standard_buttons(self.helper, self.instance, 'organization:tenantgroup_list')

    def clean_parent(self):
        parent = self.cleaned_data.get('parent')
        if parent and self.instance and self.instance.pk and parent.pk == self.instance.pk:
            raise forms.ValidationError("A tenant group cannot be its own parent.")
        return parent

# --- Tenant Form ---
class TenantForm(forms.ModelForm):
    group = forms.ModelChoiceField(
        queryset=TenantGroup.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = Tenant
        fields = ['name', 'slug', 'group', 'description', 'comments', 'tags']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'comments': forms.Textarea(attrs={'class': 'form-control', 'rows': 5}),
        }
        help_texts = {
            'slug': 'URL-friendly identifier.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'name', 'slug', 'group', 'description', 'comments', 'tags'
        )
        add_standard_buttons(self.helper, self.instance, 'organization:tenant_list')

# --- AssetHolder Form ---
class AssetHolderForm(forms.ModelForm):
    tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    # Add tags field later if using a specific widget like NetBox uses

    class Meta:
        model = AssetHolder
        fields = [
            'first_name', 'last_name', 'upn', 'email', 'tenant',
            'description', 'comments', # 'tags' excluded for now
        ]
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'upn': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'comments': forms.Textarea(attrs={'class': 'form-control', 'rows': 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True # Let crispy render the form tag

        button_text = 'Update' if self.instance and self.instance.pk else 'Create'
        cancel_url = reverse('organization:assetholder_list') # Assuming this URL name

        self.helper.layout = Layout(
            Row(
                Column('first_name', css_class='form-group col-md-6 mb-0'),
                Column('last_name', css_class='form-group col-md-6 mb-0'),
                css_class='mb-3'
            ),
            Row(
                Column('upn', css_class='form-group col-md-6 mb-0'),
                Column('email', css_class='form-group col-md-6 mb-0'),
                css_class='mb-3'
            ),
            'tenant',
            'description',
            'comments',
            # Add 'tags' here later if needed
            HTML('<div class="mt-4"></div>'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
        )

# --- Filter Forms ---

class SiteFilterForm(FilterForm):
    filterset_class = SiteFilterSet

class RegionFilterForm(FilterForm):
    filterset_class = RegionFilterSet

class SiteGroupFilterForm(FilterForm):
    filterset_class = SiteGroupFilterSet

class LocationFilterForm(FilterForm):
    filterset_class = LocationFilterSet

class TenantFilterForm(FilterForm):
    filterset_class = TenantFilterSet

class TenantGroupFilterForm(FilterForm):
    filterset_class = TenantGroupFilterSet

class AssetHolderFilterForm(FilterForm):
    filterset_class = AssetHolderFilterSet

class ContactForm(forms.ModelForm):
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = Contact
        fields = ['name', 'title', 'phone', 'email', 'web_url', 'description', 'comments', 'tags']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Sales Director'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. +1 (555) 019-2834'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'e.g. contact@example.com'}),
            'web_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'e.g. https://support.example.com'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'comments': forms.Textarea(attrs={'class': 'form-control', 'rows': 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            Row(
                Column('name', css_class='form-group col-md-6 mb-0'),
                Column('title', css_class='form-group col-md-6 mb-0'),
                css_class='mb-3'
            ),
            Row(
                Column('phone', css_class='form-group col-md-4 mb-0'),
                Column('email', css_class='form-group col-md-4 mb-0'),
                Column('web_url', css_class='form-group col-md-4 mb-0'),
                css_class='mb-3'
            ),
            'description',
            'comments',
            'tags'
        )
        add_standard_buttons(self.helper, self.instance, 'organization:contact_list')


class ContactRoleForm(forms.ModelForm):
    class Meta:
        model = ContactRole
        fields = ['name', 'slug', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control', 'slugify': 'name'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'name',
            'slug',
            'description'
        )
        add_standard_buttons(self.helper, self.instance, 'organization:contactrole_list')


class ContactAssignmentForm(forms.ModelForm):
    contact = forms.ModelChoiceField(
        queryset=Contact.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    role = forms.ModelChoiceField(
        queryset=ContactRole.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    priority = forms.ChoiceField(
        choices=[
            ('', '---------'),
            ('primary', 'Primary'),
            ('secondary', 'Secondary'),
            ('tertiary', 'Tertiary'),
            ('inactive', 'Inactive'),
        ],
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = ContactAssignment
        fields = ['contact', 'role', 'priority']

    def __init__(self, *args, **kwargs):
        content_type = kwargs.pop('content_type', None)
        object_id = kwargs.pop('object_id', None)
        super().__init__(*args, **kwargs)
        self.content_type = content_type
        self.object_id = object_id

        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'contact',
            'role',
            'priority',
        )
        button_text = 'Assign'
        self.helper.layout.append(
            HTML('<div class="mt-4"></div>')
        )
        self.helper.layout.append(
            Submit('submit', button_text, css_class='btn btn-primary')
        )
        self.helper.layout.append(
            HTML('<button type="button" class="btn btn-outline-secondary ms-2" data-bs-dismiss="modal">Cancel</button>')
        )

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.content_type and self.object_id:
            instance.content_type = self.content_type
            instance.object_id = self.object_id
        if commit:
            instance.save()
        return instance


class ContactFilterForm(FilterForm):
    filterset_class = ContactFilterSet


class ContactRoleFilterForm(FilterForm):
    filterset_class = ContactRoleFilterSet
 