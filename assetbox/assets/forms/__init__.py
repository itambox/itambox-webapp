from django import forms
from django.core.exceptions import ValidationError # Import ValidationError
# Import models from this app
from ..models import Asset, AssetRole, Manufacturer, AssetType, ComponentType, ComponentInstance, Accessory, AccessoryAssignment, Consumable, ConsumableAssignment, StatusLabel, AssetMaintenance, CustomField, CustomFieldset, Depreciation, Kit, KitItem, Supplier, Category, AssetRequest, AssetTagSequence
# Import models from other apps
from organization.models import Location, AssetHolder, Region, Site # Import Location, AssetHolder, Region, Site
from extras.models import Tag # Import Tag
from django.contrib.auth import get_user_model
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Button, Div, Fieldset, Row, Column
from django.template.loader import render_to_string
from django.urls import reverse
# --- Import FilterForm and FilterSets --- 
from core.forms import SlugModelForm, BootstrapMixin, FilterForm 
from ..filters import (
    AssetFilterSet, AssetRoleFilterSet, ManufacturerFilterSet, AssetTypeFilterSet,
    ComponentTypeFilterSet, ComponentInstanceFilterSet, AccessoryFilterSet,
    ConsumableFilterSet, StatusLabelFilterSet, AssetMaintenanceFilterSet,
    CustomFieldFilterSet, CustomFieldsetFilterSet, DepreciationFilterSet, KitFilterSet,
    SupplierFilterSet, CategoryFilterSet, AssetRequestFilterSet, AssetTagSequenceFilterSet
)
# --- End Imports --- 

User = get_user_model()

class StatusModelChoiceField(forms.ModelChoiceField):
    def to_python(self, value):
        if value in self.empty_values:
            return None
        if isinstance(value, str) and not value.isdigit():
            from django.db.models import Q
            try:
                return self.queryset.get(Q(slug=value) | Q(name__iexact=value))
            except self.queryset.model.DoesNotExist:
                raise ValidationError(self.error_messages['invalid_choice'], code='invalid_choice')
        return super().to_python(value)

class AssetForm(forms.ModelForm):
    # Define choices for related fields if needed, or rely on default widgets
    asset_type = forms.ModelChoiceField(
        queryset=AssetType.objects.select_related('manufacturer').all(),
        label="Asset Type",
        required=True,
        widget=forms.Select(attrs={
            'class': 'form-select',
            'data-tom-select': '',
            'hx-post': '',
            'hx-trigger': 'change',
            'hx-target': 'closest form',
            'hx-swap': 'outerHTML',
            'hx-vals': '{"_reload": "1"}',
            'hx-include': 'closest form',
        })
    )
    asset_role = forms.ModelChoiceField(
        queryset=AssetRole.objects.all(), 
        label="Asset Role",
        required=False, 
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''})
    )
    status = StatusModelChoiceField(
        queryset=StatusLabel.objects.all(),
        label="Status",
        required=True,
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''})
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.all(), 
        required=False, 
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''})
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
            'asset_role', 'status', 'location', 'tenant',
            'purchase_date', 'warranty_expiration',
            'purchase_cost', 'salvage_value', 'order_number', 'supplier',
            'notes', 'tags', 'requestable'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'asset_tag': forms.TextInput(attrs={'class': 'form-control'}),
            'serial_number': forms.TextInput(attrs={'class': 'form-control'}),
            'purchase_cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'salvage_value': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'order_number': forms.TextInput(attrs={'class': 'form-control'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'tenant': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'tags': forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'supplier': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
        }

    def clean_status(self):
        status = self.cleaned_data.get('status')
        if isinstance(status, str):
            from django.db.models import Q
            status_obj = StatusLabel.objects.filter(Q(slug=status) | Q(name__iexact=status)).first()
            if status_obj:
                return status_obj
            raise forms.ValidationError(f"Invalid status label: {status}")
        return status

    def __init__(self, *args, **kwargs):
        request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        # Crispy will use the request path for action by default

        button_text = 'Update' if self.instance and self.instance.pk else 'Create'
        cancel_url = reverse('assets:asset_list')

        # Get selected asset type
        asset_type_id = None
        if self.data and self.data.get('asset_type'):
            try:
                asset_type_id = int(self.data.get('asset_type'))
            except (ValueError, TypeError):
                pass
        elif request and request.GET.get('asset_type'):
            try:
                asset_type_id = int(request.GET.get('asset_type'))
            except (ValueError, TypeError):
                pass
        elif self.initial and self.initial.get('asset_type'):
            asset_type_val = self.initial.get('asset_type')
            if isinstance(asset_type_val, AssetType):
                asset_type_id = asset_type_val.pk
            else:
                asset_type_id = asset_type_val
        elif self.instance and self.instance.pk and self.instance.asset_type:
            asset_type_id = self.instance.asset_type.pk

        # If we have an asset type, get its custom fieldset and its fields
        custom_fields = []
        if asset_type_id:
            try:
                asset_type_obj = AssetType.objects.get(pk=asset_type_id)
                if asset_type_obj.custom_fieldset:
                    custom_fields = asset_type_obj.custom_fieldset.fields.all()
            except AssetType.DoesNotExist:
                pass

        # Dynamically append custom fields to form
        self.custom_field_keys = []
        for field in custom_fields:
            field_key = f"cf_{field.name}"
            self.custom_field_keys.append(field_key)
            
            # Map CustomField type to Django Form Field
            initial_value = None
            if self.instance and self.instance.pk and self.instance.custom_values:
                initial_value = self.instance.custom_values.get(field.name)
            
            form_field = None
            if field.field_type == CustomField.FIELD_TYPE_TEXT:
                form_field = forms.CharField(
                    label=field.label,
                    required=field.required,
                    initial=initial_value,
                    widget=forms.TextInput(attrs={'class': 'form-control'})
                )
            elif field.field_type == CustomField.FIELD_TYPE_NUMBER:
                form_field = forms.DecimalField(
                    label=field.label,
                    required=field.required,
                    initial=initial_value,
                    widget=forms.NumberInput(attrs={'class': 'form-control'})
                )
            elif field.field_type == CustomField.FIELD_TYPE_DATE:
                form_field = forms.DateField(
                    label=field.label,
                    required=field.required,
                    initial=initial_value,
                    widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'})
                )
            elif field.field_type == CustomField.FIELD_TYPE_BOOLEAN:
                form_field = forms.BooleanField(
                    label=field.label,
                    required=field.required,
                    initial=initial_value or False,
                    widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
                )
            elif field.field_type == CustomField.FIELD_TYPE_SELECT:
                choice_lines = [line.strip() for line in (field.choices or '').split('\n') if line.strip()]
                choices = [('', '---------')] + [(choice, choice) for choice in choice_lines]
                form_field = forms.ChoiceField(
                    label=field.label,
                    required=field.required,
                    choices=choices,
                    initial=initial_value,
                    widget=forms.Select(attrs={'class': 'form-select'})
                )
            
            if form_field:
                self.fields[field_key] = form_field

        layout_elements = [
            Div(
                Div('name', css_class='col-md-6'),
                Div('asset_tag', css_class='col-md-6'),
                css_class='row'
            ),
            Div(
                Div(
                    HTML(render_to_string('generic/includes/quick_add_button.html', {
                        'model_label': 'Asset Type',
                        'quick_add_url': 'assets:assettype_create',
                        'target_select_id': 'id_asset_type',
                        'quick_add_url_params': '_quickadd=1',
                    })),
                    'asset_type',
                    css_class='col-md-6'
                ),
                Div(
                    HTML(render_to_string('generic/includes/quick_add_button.html', {
                        'model_label': 'Asset Role',
                        'quick_add_url': 'assets:assetrole_create',
                        'target_select_id': 'id_asset_role',
                        'quick_add_url_params': '_quickadd=1',
                    })),
                    'asset_role',
                    css_class='col-md-6'
                ),
                css_class='row'
            ),
            Div(
                Div('serial_number', css_class='col-md-6'),
                Div('status', css_class='col-md-6'),
                css_class='row'
            ),
            Div(
                Div(
                    HTML(render_to_string('generic/includes/quick_add_button.html', {
                        'model_label': 'Location',
                        'quick_add_url': 'organization:location_create',
                        'target_select_id': 'id_location',
                        'quick_add_url_params': '_quickadd=1',
                    })),
                    'location',
                    css_class='col-md-6'
                ),
                Div('tenant', css_class='col-md-6'),
                css_class='row'
            ),
            Div(
                Div('purchase_date', css_class='col-md-6'),
                Div('warranty_expiration', css_class='col-md-6'),
                css_class='row'
            ),
            Div(
                Div('tags', css_class='col-md-6'),
                css_class='row'
            ),
            Div(
                Div('purchase_cost', css_class='col-md-3'),
                Div('salvage_value', css_class='col-md-3'),
                Div('order_number', css_class='col-md-3'),
                Div('supplier', css_class='col-md-3'),
                css_class='row'
            ),
        ]

        if self.custom_field_keys:
            cf_divs = []
            for i in range(0, len(self.custom_field_keys), 2):
                chunk = self.custom_field_keys[i:i+2]
                row_cols = [Div(key, css_class='col-md-6') for key in chunk]
                cf_divs.append(Div(*row_cols, css_class='row'))
            layout_elements.append(
                Fieldset(
                    'Custom Specifications',
                    *cf_divs,
                    css_class='mb-4 border p-3 rounded bg-light'
                )
            )

        layout_elements.extend([
            'notes',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        ])

        self.helper.layout = Layout(*layout_elements)

    def save(self, commit=True):
        instance = super().save(commit=False)
        
        # Collect dynamic custom fields and populate custom_values JSON
        custom_values = {}
        for key, value in self.cleaned_data.items():
            if key.startswith('cf_'):
                field_name = key[3:] # Remove 'cf_'
                if value is not None:
                    if isinstance(value, (int, float, bool)):
                        custom_values[field_name] = value
                    elif hasattr(value, 'isoformat'): # date/datetime
                        custom_values[field_name] = value.isoformat()
                    else:
                        custom_values[field_name] = str(value)
                else:
                    custom_values[field_name] = None
        
        instance.custom_values = custom_values
        
        if commit:
            instance.save()
            self.save_m2m() # Ensure tags are saved if commit=True
        return instance


# --- AssetRole (Asset Role) Form ---
class AssetRoleForm(forms.ModelForm):
    class Meta:
        model = AssetRole
        fields = ['name', 'slug', 'description', 'color', 'tags']
        
    color = forms.CharField(
        max_length=7, 
        required=False, 
        widget=forms.TextInput(attrs={
            'type': 'color',
            'class': 'form-control form-control-color'
        }),
        help_text="Choose a color for this Asset Role"
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

# --- StatusLabel Form ---
class StatusLabelForm(forms.ModelForm):
    class Meta:
        model = StatusLabel
        fields = ['name', 'slug', 'type', 'description', 'color']
        
    color = forms.CharField(
        max_length=7, 
        required=False, 
        widget=forms.TextInput(attrs={
            'type': 'color',
            'class': 'form-control form-control-color'
        }),
        help_text="Choose a color for this Status Label"
    )
    
    type = forms.ChoiceField(
        choices=StatusLabel.TYPE_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        cancel_url = reverse('assets:statuslabel_list') 
        self.helper.layout = Layout(
            Fieldset(
                '', # No legend
                'name',
                'slug',
                'type',
                'description',
                'color',
            ),
            Row(
                Column(Submit('submit', 'Save', css_class='btn btn-primary'), css_class='col'),
                Column(Button('cancel', 'Cancel', css_class='btn btn-secondary', onclick=f"window.location.href='{cancel_url}'"), css_class='col text-end')
            )
        )
        
        # If the instance has a color value, format it for the colorpicker
        if self.instance and self.instance.color:
            self.initial['color'] = f'#{self.instance.color}'
            
        self.fields['slug'].widget.attrs['slugify'] = 'name'

    def clean_color(self):
        """Clean and format the color value, removing # prefix if present."""
        color = self.cleaned_data.get('color')
        if color and color.startswith('#'):
            cleaned_color = color[1:]
            if len(cleaned_color) == 6:
                return cleaned_color
            else:
                raise forms.ValidationError("Ensure the color hex code is 6 characters long (after removing '#').")
        elif not color:
            return ''
        if len(color) == 6:
            return color
        elif len(color) == 0:
            return ''
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
            'cpu', 'ram_gb', 'storage_capacity_gb', 'storage_type', 'gpu', 'eol_months',
            'description', 'comments', 'tags', 'requestable'
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
            'eol_months': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
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
                    Column('slug', css_class='col-md-4'),
                    Column('part_number', css_class='col-md-4'),
                    Column('eol_months', css_class='col-md-4')
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


class ComponentTypeForm(SlugModelForm):
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
        model = ComponentType
        fields = ['manufacturer', 'name', 'slug', 'category', 'part_number', 'specs', 'description', 'tags']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control', 'slugify': 'name'}),
            'part_number': forms.TextInput(attrs={'class': 'form-control'}),
            'specs': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }
        help_texts = {
            'slug': 'URL-friendly identifier. Leave blank to auto-generate.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.fields['slug'].widget.attrs['slugify'] = 'name'
        
        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = self.instance.get_absolute_url() if self.instance.pk else reverse('assets:componenttype_list')
        
        self.helper.layout = Layout(
            Row(
                Column('manufacturer', css_class='col-md-6'),
                Column('name', css_class='col-md-6')
            ),
            Row(
                Column('slug', css_class='col-md-6'),
                Column('part_number', css_class='col-md-6')
            ),
            Row(
                Column('category', css_class='col-md-6'),
                Column('specs', css_class='col-md-6')
            ),
            'description',
            'tags',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )


class ComponentInstanceForm(forms.ModelForm):
    component_type = forms.ModelChoiceField(
        queryset=ComponentType.objects.all(),
        label="Component Model",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    parent_asset = forms.ModelChoiceField(
        queryset=Asset.objects.all(),
        label="Asset Installed In",
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    purchase_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        required=False
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'data-tomselect-tags': 'true'}),
        label="Tags"
    )

    class Meta:
        model = ComponentInstance
        fields = ['component_type', 'serial_number', 'parent_asset', 'status', 'purchase_date', 'purchase_cost', 'notes', 'tags']
        widgets = {
            'serial_number': forms.TextInput(attrs={'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'purchase_cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        
        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = self.instance.get_absolute_url() if self.instance.pk else reverse('assets:componentinstance_list')
        
        self.helper.layout = Layout(
            Row(
                Column('component_type', css_class='col-md-6'),
                Column('serial_number', css_class='col-md-6')
            ),
            Row(
                Column('parent_asset', css_class='col-md-6'),
                Column('status', css_class='col-md-6')
            ),
            Row(
                Column('purchase_date', css_class='col-md-6'),
                Column('purchase_cost', css_class='col-md-6')
            ),
            'notes',
            'tags',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )


class ComponentTypeFilterForm(FilterForm):
    filterset_class = ComponentTypeFilterSet


class ComponentInstanceFilterForm(FilterForm):
    filterset_class = ComponentInstanceFilterSet


class AccessoryForm(SlugModelForm):
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
        model = Accessory
        fields = ['manufacturer', 'name', 'slug', 'category', 'part_number', 'qty', 'min_qty', 'allow_overallocate', 'notes', 'tags', 'tenant']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control', 'slugify': 'name'}),
            'part_number': forms.TextInput(attrs={'class': 'form-control'}),
            'qty': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'min_qty': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'allow_overallocate': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.fields['slug'].widget.attrs['slugify'] = 'name'
        
        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = self.instance.get_absolute_url() if self.instance.pk else reverse('assets:accessory_list')
        
        self.helper.layout = Layout(
            Row(
                Column('manufacturer', css_class='col-md-6'),
                Column('name', css_class='col-md-6')
            ),
            Row(
                Column('slug', css_class='col-md-6'),
                Column('part_number', css_class='col-md-6')
            ),
            Row(
                Column('category', css_class='col-md-4'),
                Column('qty', css_class='col-md-4'),
                Column('min_qty', css_class='col-md-4')
            ),
            Row(
                Column('tenant', css_class='col-md-6'),
                Column('tags', css_class='col-md-6')
            ),
            Div(
                'allow_overallocate',
                css_class='mb-3 form-check'
            ),
            'notes',
            'tags',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )


class AccessoryCheckoutForm(forms.Form):
    assigned_holder = forms.ModelChoiceField(
        queryset=AssetHolder.objects.all().order_by('last_name', 'first_name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Assign to Asset Holder"
    )
    assigned_location = forms.ModelChoiceField(
        queryset=Location.objects.all().select_related('site').order_by('site__name', 'name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Assign to Location"
    )
    qty = forms.IntegerField(
        initial=1,
        min_value=1,
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
        label="Quantity"
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        label="Notes"
    )

    def __init__(self, *args, **kwargs):
        self.accessory = kwargs.pop('accessory', None)
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            'assigned_holder',
            HTML('<p class="text-muted text-center my-2">OR</p>'),
            'assigned_location',
            'qty',
            'notes'
        )

    def clean(self):
        cleaned_data = super().clean()
        holder = cleaned_data.get('assigned_holder')
        location = cleaned_data.get('assigned_location')
        qty = cleaned_data.get('qty')

        if not holder and not location:
            raise ValidationError("You must select either an Asset Holder or a Location.")
        if holder and location:
            raise ValidationError("Please select either an Asset Holder OR a Location, not both.")

        if self.accessory and qty:
            remaining = self.accessory.remaining_qty
            if not self.accessory.allow_overallocate and qty > remaining:
                raise ValidationError(f"Cannot checkout {qty} units. Only {remaining} units are currently in stock.")

        return cleaned_data


class ConsumableForm(SlugModelForm):
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
        model = Consumable
        fields = ['manufacturer', 'name', 'slug', 'category', 'part_number', 'qty', 'min_qty', 'allow_overallocate', 'notes', 'tags', 'tenant']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control', 'slugify': 'name'}),
            'part_number': forms.TextInput(attrs={'class': 'form-control'}),
            'qty': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'min_qty': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'allow_overallocate': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.fields['slug'].widget.attrs['slugify'] = 'name'
        
        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = self.instance.get_absolute_url() if self.instance.pk else reverse('assets:consumable_list')
        
        self.helper.layout = Layout(
            Row(
                Column('manufacturer', css_class='col-md-6'),
                Column('name', css_class='col-md-6')
            ),
            Row(
                Column('slug', css_class='col-md-6'),
                Column('part_number', css_class='col-md-6')
            ),
            Row(
                Column('category', css_class='col-md-4'),
                Column('qty', css_class='col-md-4'),
                Column('min_qty', css_class='col-md-4')
            ),
            Row(
                Column('tenant', css_class='col-md-6'),
                Column('tags', css_class='col-md-6')
            ),
            Div(
                'allow_overallocate',
                css_class='mb-3 form-check'
            ),
            'notes',
            'tags',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )


class ConsumableCheckoutForm(forms.Form):
    assigned_holder = forms.ModelChoiceField(
        queryset=AssetHolder.objects.all().order_by('last_name', 'first_name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Assign to Asset Holder"
    )
    assigned_location = forms.ModelChoiceField(
        queryset=Location.objects.all().select_related('site').order_by('site__name', 'name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Assign to Location"
    )
    qty = forms.IntegerField(
        initial=1,
        min_value=1,
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
        label="Quantity"
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        label="Notes"
    )

    def __init__(self, *args, **kwargs):
        self.consumable = kwargs.pop('consumable', None)
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            'assigned_holder',
            HTML('<p class="text-muted text-center my-2">OR</p>'),
            'assigned_location',
            'qty',
            'notes'
        )

    def clean(self):
        cleaned_data = super().clean()
        holder = cleaned_data.get('assigned_holder')
        location = cleaned_data.get('assigned_location')
        qty = cleaned_data.get('qty')

        if not holder and not location:
            raise ValidationError("You must select either an Asset Holder or a Location.")
        if holder and location:
            raise ValidationError("Please select either an Asset Holder OR a Location, not both.")

        if self.consumable and qty:
            remaining = self.consumable.remaining_qty
            if not self.consumable.allow_overallocate and qty > remaining:
                raise ValidationError(f"Cannot checkout {qty} units. Only {remaining} units are currently in stock.")

        return cleaned_data


class AccessoryFilterForm(FilterForm):
    filterset_class = AccessoryFilterSet


class ConsumableFilterForm(FilterForm):
    filterset_class = ConsumableFilterSet


class StatusLabelFilterForm(FilterForm):
    filterset_class = StatusLabelFilterSet


class AssetMaintenanceForm(forms.ModelForm):
    asset = forms.ModelChoiceField(
        queryset=Asset.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Asset"
    )
    maintenance_type = forms.ChoiceField(
        choices=AssetMaintenance.MAINTENANCE_TYPE_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Maintenance Type"
    )
    start_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        label="Start Date"
    )
    completion_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        required=False,
        label="Completion Date"
    )

    class Meta:
        model = AssetMaintenance
        fields = [
            'asset', 'supplier', 'maintenance_type', 'cost',
            'start_date', 'completion_date', 'notes'
        ]
        widgets = {
            'supplier': forms.TextInput(attrs={'class': 'form-control'}),
            'cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True

        button_text = 'Update' if self.instance and self.instance.pk else 'Create'
        cancel_url = reverse('assets:assetmaintenance_list')

        self.helper.layout = Layout(
            Row(
                Column('asset', css_class='col-md-6'),
                Column('supplier', css_class='col-md-6')
            ),
            Row(
                Column('maintenance_type', css_class='col-md-6'),
                Column('cost', css_class='col-md-6')
            ),
            Row(
                Column('start_date', css_class='col-md-6'),
                Column('completion_date', css_class='col-md-6')
            ),
            'notes',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )


class AssetMaintenanceFilterForm(FilterForm):
    filterset_class = AssetMaintenanceFilterSet


class CustomFieldForm(forms.ModelForm):
    class Meta:
        model = CustomField
        fields = ['name', 'label', 'field_type', 'choices', 'required']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'label': forms.TextInput(attrs={'class': 'form-control'}),
            'field_type': forms.Select(attrs={'class': 'form-select'}),
            'choices': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Value 1\nValue 2'}),
            'required': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.fields['name'].widget.attrs['slugify'] = 'label'
        
        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = reverse('assets:customfield_list')
        
        self.helper.layout = Layout(
            'label',
            'name',
            'field_type',
            'choices',
            Div('required', css_class='mb-3 form-check'),
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )


class CustomFieldsetForm(forms.ModelForm):
    class Meta:
        model = CustomFieldset
        fields = ['name', 'fields']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'fields': forms.SelectMultiple(attrs={'class': 'form-select', 'size': 10}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        
        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = reverse('assets:customfieldset_list')
        
        self.helper.layout = Layout(
            'name',
            'fields',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )


class DepreciationForm(forms.ModelForm):
    class Meta:
        model = Depreciation
        fields = ['name', 'months']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'months': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        
        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = reverse('assets:depreciation_list')
        
        self.helper.layout = Layout(
            'name',
            'months',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )


class KitForm(forms.ModelForm):
    class Meta:
        model = Kit
        fields = ['name', 'description', 'tenant']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        
        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = reverse('assets:kit_list')
        
        self.helper.layout = Layout(
            'name',
            'description',
            'tenant',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )


class KitItemForm(forms.ModelForm):
    class Meta:
        model = KitItem
        fields = ['kit', 'asset_type', 'accessory', 'license', 'qty']
        widgets = {
            'kit': forms.Select(attrs={'class': 'form-select'}),
            'asset_type': forms.Select(attrs={'class': 'form-select'}),
            'accessory': forms.Select(attrs={'class': 'form-select'}),
            'license': forms.Select(attrs={'class': 'form-select'}),
            'qty': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        
        button_text = 'Update' if self.instance.pk else 'Create'
        # Redirect to the kit's detail or kit list
        cancel_url = self.instance.kit.get_absolute_url() if (self.instance.pk and self.instance.kit) else reverse('assets:kit_list')
        
        self.helper.layout = Layout(
            'kit',
            'asset_type',
            'accessory',
            'license',
            'qty',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )

    def clean(self):
        cleaned_data = super().clean()
        asset_type = cleaned_data.get('asset_type')
        accessory = cleaned_data.get('accessory')
        license_item = cleaned_data.get('license')

        targets = [asset_type, accessory, license_item]
        filled = [t for t in targets if t is not None]
        if len(filled) == 0:
            raise ValidationError("A kit item must select either an Asset Type, Accessory, or License.")
        if len(filled) > 1:
            raise ValidationError("A kit item cannot select more than one target (must be either Asset Type OR Accessory OR License).")
        return cleaned_data


class KitCheckoutForm(forms.Form):
    assigned_holder = forms.ModelChoiceField(
        queryset=AssetHolder.objects.all().order_by('last_name', 'first_name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Assign to Asset Holder"
    )
    assigned_location = forms.ModelChoiceField(
        queryset=Location.objects.all().select_related('site').order_by('site__name', 'name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Assign to Location"
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        label="Notes"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            'assigned_holder',
            HTML('<p class="text-muted text-center my-2">OR</p>'),
            'assigned_location',
            'notes'
        )

    def clean(self):
        cleaned_data = super().clean()
        holder = cleaned_data.get('assigned_holder')
        location = cleaned_data.get('assigned_location')

        if not holder and not location:
            raise ValidationError("You must select either an Asset Holder or a Location.")
        if holder and location:
            raise ValidationError("Please select either an Asset Holder OR a Location, not both.")

        return cleaned_data


class CustomFieldFilterForm(FilterForm):
    filterset_class = CustomFieldFilterSet


class CustomFieldsetFilterForm(FilterForm):
    filterset_class = CustomFieldsetFilterSet


class DepreciationFilterForm(FilterForm):
    filterset_class = DepreciationFilterSet


class KitFilterForm(FilterForm):
    filterset_class = KitFilterSet


class SupplierForm(SlugModelForm, BootstrapMixin):
    class Meta:
        model = Supplier
        fields = ['name', 'website', 'contact_email', 'contact_phone', 'contact_name', 'address', 'notes', 'tags']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'website': forms.URLInput(attrs={'class': 'form-control'}),
            'contact_email': forms.EmailInput(attrs={'class': 'form-control'}),
            'contact_phone': forms.TextInput(attrs={'class': 'form-control'}),
            'contact_name': forms.TextInput(attrs={'class': 'form-control'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'tags': forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''}),
        }


class CategoryForm(SlugModelForm, BootstrapMixin):
    class Meta:
        model = Category
        fields = ['name', 'color', 'description', 'email_on_checkout', 'email_on_checkin', 'require_acceptance', 'email_eula', 'tags']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'color': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '00ff00'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'tags': forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''}),
        }


class AssetRequestForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = AssetRequest
        fields = ['asset', 'asset_type', 'notes']
        widgets = {
            'asset': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'asset_type': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }


class AssetRequestResponseForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = AssetRequest
        fields = ['status', 'response_notes']
        widgets = {
            'status': forms.Select(attrs={'class': 'form-select'}),
            'response_notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }


class SupplierFilterForm(FilterForm):
    filterset_class = SupplierFilterSet


class CategoryFilterForm(FilterForm):
    filterset_class = CategoryFilterSet


class AssetRequestFilterForm(FilterForm):
    filterset_class = AssetRequestFilterSet


class AssetTagSequenceForm(forms.ModelForm):
    class Meta:
        model = AssetTagSequence
        fields = ['prefix', 'next_value', 'zero_padding']
        widgets = {
            'prefix': forms.TextInput(attrs={'class': 'form-control'}),
            'next_value': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'zero_padding': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'max': 20}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True

        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = reverse('assets:assettagsequence_list')

        self.helper.layout = Layout(
            'prefix',
            'next_value',
            'zero_padding',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )


class AssetTagSequenceFilterForm(FilterForm):
    filterset_class = AssetTagSequenceFilterSet