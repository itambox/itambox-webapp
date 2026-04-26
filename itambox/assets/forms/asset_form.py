from django import forms
from django.urls import reverse
from django.template.loader import render_to_string
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Row, Column, Fieldset, Div

from extras.models import Tag, CustomField
from organization.models import Location
from ..models import Asset, AssetType, AssetRole, StatusLabel

from .fields import StatusModelChoiceField


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
                    css_class='mb-4 border p-3 rounded'
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
