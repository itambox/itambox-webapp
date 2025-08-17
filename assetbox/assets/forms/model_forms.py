from django import forms
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.template.loader import render_to_string
from django.contrib.auth import get_user_model
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Row, Column, Fieldset, Div, Button


from core.forms import SlugModelForm, BootstrapMixin, ColorFieldFormMixin
from extras.models import Tag, CustomField
from organization.models import Location
from ..models import (
    Asset, AssetRole, StatusLabel, Manufacturer, AssetType,
    Depreciation, Supplier, Category, AssetRequest, AssetTagSequence
)

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

        button_text = 'Update' if self.instance and self.instance.pk else 'Create'
        cancel_url = reverse('assets:asset_list')

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

        custom_fields = []
        if asset_type_id:
            try:
                asset_type_obj = AssetType.objects.get(pk=asset_type_id)
                if asset_type_obj.custom_fieldset:
                    custom_fields = asset_type_obj.custom_fieldset.fields.all()
            except AssetType.DoesNotExist:
                pass

        self.custom_field_keys = []
        for field in custom_fields:
            field_key = f"cf_{field.name}"
            self.custom_field_keys.append(field_key)
            
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
        
        custom_values = {}
        for key, value in self.cleaned_data.items():
            if key.startswith('cf_'):
                field_name = key[3:]
                if value is not None:
                    if isinstance(value, (int, float, bool)):
                        custom_values[field_name] = value
                    elif hasattr(value, 'isoformat'):
                        custom_values[field_name] = value.isoformat()
                    else:
                        custom_values[field_name] = str(value)
                else:
                    custom_values[field_name] = None
        
        instance.custom_values = custom_values
        
        if commit:
            instance.save()
            self.save_m2m()
        return instance

class AssetRoleForm(ColorFieldFormMixin, forms.ModelForm):
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
        cancel_url = reverse('assets:assetrole_list') 
        self.helper.layout = Layout(
            Fieldset(
                '',
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

class StatusLabelForm(ColorFieldFormMixin, forms.ModelForm):
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'data-tomselect-tags': 'true'}),
    )

    class Meta:
        model = StatusLabel
        fields = ['name', 'slug', 'type', 'description', 'color', 'tags']
        
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
                '',
                'name',
                'slug',
                'type',
                'description',
                'color',
                'tags',
            ),
            Row(
                Column(Submit('submit', 'Save', css_class='btn btn-primary'), css_class='col'),
                Column(Button('cancel', 'Cancel', css_class='btn btn-secondary', onclick=f"window.location.href='{cancel_url}'"), css_class='col text-end')
            )
        )
        self.fields['slug'].widget.attrs['slugify'] = 'name'

class ManufacturerForm(SlugModelForm):
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'data-tomselect-tags': 'true'}),
    )

    class Meta:
        model = Manufacturer
        fields = ['name', 'slug', 'description', 'tags']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control', 'slugify': 'name'}),
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
        self.fields['slug'].widget.attrs['slugify'] = 'name'

        button_text = 'Update' if self.instance and self.instance.pk else 'Create'
        self.helper.layout = Layout(
            'name',
            'slug',
            'description',
            'tags',
            HTML('<div class="mt-4">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML('<a href="{0}" class="btn btn-outline-secondary ms-2">Cancel</a>'.format(reverse('assets:manufacturer_list'))),
            HTML('</div>')
        )

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
            'category', 'custom_fieldset', 'depreciation', 'image',
            'description', 'comments', 'tags', 'requestable'
        ]
        widgets = {
            'model': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control', 'slugify': 'model'}), 
            'part_number': forms.TextInput(attrs={'class': 'form-control'}),
            'cpu': forms.TextInput(attrs={'class': 'form-control'}),
            'ram_gb': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'storage_capacity_gb': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'storage_type': forms.Select(attrs={'class': 'form-select'}),
            'gpu': forms.TextInput(attrs={'class': 'form-control'}),
            'eol_months': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'custom_fieldset': forms.Select(attrs={'class': 'form-select'}),
            'depreciation': forms.Select(attrs={'class': 'form-select'}),
            'image': forms.FileInput(attrs={'class': 'form-control'}),
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
        self.fields['slug'].widget.attrs['slugify'] = 'model'
        
        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = self.instance.get_absolute_url() if self.instance.pk else reverse('assets:assettype_list')
        
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
                'image',
                'description'
            ),
            Fieldset(
                'Classification',
                Row(
                    Column('category', css_class='col-md-6'),
                    Column('custom_fieldset', css_class='col-md-6')
                ),
                Row(
                    Column('depreciation', css_class='col-md-12'),
                ),
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
                'tags'
            ),
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


class SupplierForm(SlugModelForm, BootstrapMixin):
    class Meta:
        model = Supplier
        fields = ['name', 'slug', 'website', 'contact_email', 'contact_phone', 'contact_name', 'address', 'notes', 'tags']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control', 'slugify': 'name'}),
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
        fields = ['name', 'slug', 'color', 'description', 'applies_to', 'email_on_checkout', 'email_on_checkin', 'require_acceptance', 'email_eula', 'tags']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control', 'slugify': 'name'}),
            'color': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '00ff00'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'applies_to': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': '["asset", "accessory", "license"]'}),
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
