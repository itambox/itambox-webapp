from django import forms
from django.core.exceptions import ValidationError # Import ValidationError
# Import models from this app
from .models import Asset, AssetRole, Manufacturer, AssetType # Keep Asset, AssetRole, Manufacturer, AssetType
# Import models from other apps
from organization.models import Location, AssetHolder, Region, Site # Import Location, AssetHolder, Region, Site
from extras.models import ConfigTemplate, Tag # Import ConfigTemplate
from django.contrib.auth import get_user_model
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Button, Div, Fieldset, Row, Column
from django.urls import reverse
# --- Import FilterForm and FilterSets --- 
from core.forms import SlugModelForm, BootstrapMixin, FilterForm 
from .filters import AssetFilterSet, AssetRoleFilterSet, ManufacturerFilterSet, AssetTypeFilterSet
# --- End Imports --- 

User = get_user_model()

class AssetForm(forms.ModelForm):
    # Define choices for related fields if needed, or rely on default widgets
    asset_type = forms.ModelChoiceField(
        queryset=AssetType.objects.select_related('manufacturer').all(),
        label="Asset Type",
        required=True,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    asset_role = forms.ModelChoiceField(
        queryset=AssetRole.objects.all(), 
        label="Asset Role",
        required=False, 
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.all(), 
        required=False, 
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    purchase_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}), 
        required=False
    )
    warranty_expiration = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}), 
        required=False
    )

    class Meta:
        model = Asset
        fields = [
            'name', 'asset_tag', 'serial_number', 'asset_type',
            'asset_role', 'status', 'location',
            'purchase_date', 'warranty_expiration', 'notes', 'tags'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'asset_tag': forms.TextInput(attrs={'class': 'form-control'}),
            'serial_number': forms.TextInput(attrs={'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        # Crispy will use the request path for action by default

        button_text = 'Update' if self.instance and self.instance.pk else 'Create'
        cancel_url = reverse('assets:asset_list')
        # If updating, maybe cancel goes back to detail view?
        # if self.instance and self.instance.pk:
        #    cancel_url = reverse('assets:asset_detail', kwargs={'pk': self.instance.pk})

        self.helper.layout = Layout(
            Div(
                Div('name', css_class='col-md-6'),
                Div('asset_tag', css_class='col-md-6'),
                css_class='row'
            ),
            Div(
                Div('asset_type', css_class='col-md-6'),
                Div('asset_role', css_class='col-md-6'),
                css_class='row'
            ),
            Div(
                Div('serial_number', css_class='col-md-6'),
                Div('status', css_class='col-md-6'),
                css_class='row'
            ),
            Div(
                Div('location', css_class='col-md-6'),
                Div('purchase_date', css_class='col-md-6'),
                css_class='row'
            ),
            Div(
                Div('warranty_expiration', css_class='col-md-6'),
                Div('tags', css_class='col-md-6'),
                css_class='row'
            ),
            'notes',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )

# --- AssetRole (Asset Role) Form ---
class AssetRoleForm(forms.ModelForm):
    class Meta:
        model = AssetRole
        fields = ['name', 'slug', 'description', 'color', 'config_template', 'tags']
        
    color = forms.CharField(
        max_length=7, 
        required=False, 
        widget=forms.TextInput(attrs={
            'type': 'color',
            'class': 'form-control form-control-color'
        }),
        help_text="Choose a color for this Asset Role"
    )
    
    config_template = forms.ModelChoiceField(
        queryset=ConfigTemplate.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        # Use standardized URL name
        cancel_url = reverse('assets:assetrole_list') 
        self.helper.layout = Layout(
            Fieldset(
                '', # No legend
                'name',
                'slug',
                'description',
                'color',
                'config_template',
                'tags'
            ),
            Row(
                Column(Submit('submit', 'Save', css_class='btn btn-primary'), css_class='col'),
                Column(Button('cancel', 'Cancel', css_class='btn btn-secondary', onclick=f"window.location.href='{cancel_url}'"), css_class='col text-end')
            )
        )
        
        # If the instance has a color value, format it for the colorpicker
        if self.instance and self.instance.color:
            self.initial['color'] = f'#{self.instance.color}'
    
    def clean_color(self):
        """Clean and format the color value, removing # prefix if present."""
        color = self.cleaned_data.get('color')
        if color and color.startswith('#'):
            # Strip the '#' and validate length
            cleaned_color = color[1:]
            if len(cleaned_color) == 6:
                return cleaned_color
            else:
                raise forms.ValidationError("Ensure the color hex code is 6 characters long (after removing '#').")
        elif not color:
            # Allow empty color
            return ''
        # Handle case where color doesn't start with # but might be valid
        if len(color) == 6:
            return color
        elif len(color) == 0:
            return '' # Allow empty
        else:
            raise forms.ValidationError("Ensure the color hex code is 6 characters long.")

# --- Manufacturer Form ---
# Use SlugModelForm for ManufacturerForm as well
class ManufacturerForm(SlugModelForm):
    class Meta:
        model = Manufacturer
        fields = ['name', 'slug', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control', 'slugify': 'name'}), # Add slugify attribute
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
        
        # Set slug source field
        self.fields['slug'].widget.attrs['slugify'] = 'name'

        # Define button layout
        button_text = 'Update' if self.instance and self.instance.pk else 'Create'
        self.helper.layout = Layout(
            'name',
            'slug',
            'description',
            HTML('<div class="mt-4">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML('<a href="{0}" class="btn btn-outline-secondary ms-2">Cancel</a>'.format(reverse('assets:manufacturer_list'))),
            HTML('</div>')
        )

# --- Asset Type Form ---
# Inherit from SlugModelForm
class AssetTypeForm(SlugModelForm):
    manufacturer = forms.ModelChoiceField(
        queryset=Manufacturer.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'data-tomselect-tags': 'true'}),
        label="Tags"
    )

    class Meta:
        model = AssetType
        fields = [
            'manufacturer', 'part_number', 'model', 'slug', 
            'cpu', 'ram_gb', 'storage_capacity_gb', 'storage_type', 'gpu',
            'description', 'comments', 'tags'
        ]
        widgets = {
            'model': forms.TextInput(attrs={'class': 'form-control'}),
            # Add the slugify attribute to the slug field
            'slug': forms.TextInput(attrs={'class': 'form-control', 'slugify': 'model'}), 
            'part_number': forms.TextInput(attrs={'class': 'form-control'}),
            'cpu': forms.TextInput(attrs={'class': 'form-control'}),
            'ram_gb': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'storage_capacity_gb': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'storage_type': forms.Select(attrs={'class': 'form-select'}),
            'gpu': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'comments': forms.Textarea(attrs={'class': 'form-control', 'rows': 5}),
        }
        help_texts = {
            'slug': 'URL-friendly identifier. Leave blank to auto-generate.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        
        # Set slug source field
        self.fields['slug'].widget.attrs['slugify'] = 'model'
        
        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = self.instance.get_absolute_url() if self.instance.pk else reverse('assets:assettype_list')
        
        # Use Crispy Forms layout for better structure
        self.helper.layout = Layout(
            Fieldset(
                'General Information',
                Row(
                    Column('manufacturer', css_class='col-md-6'),
                    Column('model', css_class='col-md-6')
                ),
                Row(
                    Column('slug', css_class='col-md-6'),
                    Column('part_number', css_class='col-md-6')
                ),
                'description'
            ),
            Fieldset(
                'Specifications (Optional)',
                Row(
                    Column('cpu', css_class='col-md-6'),
                    Column('ram_gb', css_class='col-md-6')
                ),
                Row(
                    Column('storage_capacity_gb', css_class='col-md-4'),
                    Column('storage_type', css_class='col-md-4'),
                    Column('gpu', css_class='col-md-4')
                )
            ),
            Fieldset(
                'Additional Information',
                'comments',
                'tags' # Ensure tags field is included
            ),
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )

# SiteForm, RegionForm, SiteGroupForm were moved to organization/forms.py 

# --- Form for Checking Out Asset (Modal) ---
class AssetCheckOutForm(forms.Form):
    # assigned_to field commented out
    asset_holder = forms.ModelChoiceField(
        queryset=AssetHolder.objects.all().order_by('last_name', 'first_name'),
        required=False, # Not required anymore
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Assign to Asset Holder"
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.all().select_related('site').order_by('site__name', 'name'), # Add location field
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Assign to Location"
    )
    # Optional Notes field for log?
    # notes = forms.CharField(widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}), required=False)

    def clean(self):
        # Reinstate clean method
        cleaned_data = super().clean()
        asset_holder = cleaned_data.get("asset_holder")
        location = cleaned_data.get("location")

        if not asset_holder and not location:
            raise ValidationError(
                "You must select either an Asset Holder or a Location.",
                code='assignment_or_location_required'
            )
        
        if asset_holder and location:
            raise ValidationError(
                "Please select either an Asset Holder OR a Location, not both.",
                code='multiple_assignments_locations'
            )
        return cleaned_data 

    # Add FormHelper for crispy rendering
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        # Important: Don't render the <form> tag via crispy, template will handle it
        self.helper.form_tag = False
        # Define layout including fields and buttons for crispy to render
        self.helper.layout = Layout(
            'asset_holder',
            HTML('<p class="text-muted text-center my-2">OR</p>'), # Add separator
            'location',
            # 'notes', # Uncomment if notes field is added
        )

# --- Asset Filter Form --- 
class AssetFilterForm(FilterForm):
    filterset_class = AssetFilterSet

# --- AssetRole Filter Form --- 
class AssetRoleFilterForm(FilterForm):
    filterset_class = AssetRoleFilterSet

# --- Manufacturer Filter Form --- 
class ManufacturerFilterForm(FilterForm):
    filterset_class = ManufacturerFilterSet

# --- AssetType Filter Form --- 
class AssetTypeFilterForm(FilterForm):
    filterset_class = AssetTypeFilterSet