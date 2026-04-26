from django import forms
from django.urls import reverse
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Div, Row, Column, Fieldset

from core.forms import SlugModelForm
from extras.models import Tag
from ..models import AssetType, Manufacturer


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
            'eol_months',
            'category', 'custom_fieldset', 'depreciation', 'image',
            'description', 'comments', 'tags', 'requestable'
        ]
        widgets = {
            'model': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control', 'slugify': 'model'}),
            'part_number': forms.TextInput(attrs={'class': 'form-control'}),
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

        # Set up HTMX attributes to reload the form when custom_fieldset choice changes
        self.fields['custom_fieldset'].widget.attrs.update({
            'hx-post': '',
            'hx-trigger': 'change',
            'hx-target': 'closest form',
            'hx-swap': 'outerHTML',
            'hx-vals': '{"_reload": "1"}',
            'hx-include': 'closest form',
        })

        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = self.instance.get_absolute_url() if self.instance.pk else reverse('assets:assettype_list')

        # Determine the selected custom fieldset
        custom_fieldset_id = None
        if self.data and self.data.get('custom_fieldset'):
            try:
                custom_fieldset_id = int(self.data.get('custom_fieldset'))
            except (ValueError, TypeError):
                pass
        elif self.initial and self.initial.get('custom_fieldset'):
            custom_fieldset_val = self.initial.get('custom_fieldset')
            if hasattr(custom_fieldset_val, 'pk'):
                custom_fieldset_id = custom_fieldset_val.pk
            else:
                custom_fieldset_id = custom_fieldset_val
        elif self.instance and self.instance.pk and self.instance.custom_fieldset:
            custom_fieldset_id = self.instance.custom_fieldset.pk

        custom_fields = []
        if custom_fieldset_id:
            from extras.models import CustomFieldset, CustomField
            try:
                fieldset_obj = CustomFieldset.objects.get(pk=custom_fieldset_id)
                custom_fields = fieldset_obj.fields.all()
            except CustomFieldset.DoesNotExist:
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

        # Build dynamic form layout
        layout_elements = [
            Fieldset(
                'General Information',
                Row(
                    Column('manufacturer', css_class='col-md-6'),
                    Column('model', css_class='col-md-6')
                ),
                Row(
                    Column('part_number', css_class='col-md-4'),
                    Column('slug', css_class='col-md-4'),
                    Column('eol_months', css_class='col-md-4')
                ),
                Row(
                    Column('image', css_class='col-md-6'),
                    Column('description', css_class='col-md-6')
                ),
            ),
            Fieldset(
                'Classification & Financial',
                Row(
                    Column('category', css_class='col-md-4'),
                    Column('custom_fieldset', css_class='col-md-4'),
                    Column('depreciation', css_class='col-md-4')
                ),
            ),
        ]

        if self.custom_field_keys:
            cf_divs = []
            for i in range(0, len(self.custom_field_keys), 2):
                chunk = self.custom_field_keys[i:i+2]
                row_cols = [Column(key, css_class='col-md-6') for key in chunk]
                cf_divs.append(Row(*row_cols))
            
            layout_elements.append(
                Fieldset(
                    'Specifications',
                    *cf_divs
                )
            )
        else:
            layout_elements.append(
                Fieldset(
                    'Specifications',
                    HTML(
                        '<div class="alert alert-info d-flex align-items-center mb-0" role="alert">'
                        '  <i class="mdi mdi-information-outline me-2"></i>'
                        '  <div>Select a Custom Fieldset under Classification & Financial to add specifications.</div>'
                        '</div>'
                    )
                )
            )

        layout_elements.extend([
            Fieldset(
                'Additional Information',
                'comments',
                Row(
                    Column('tags', css_class='col-md-8'),
                    Column('requestable', css_class='col-md-4 mt-4')
                )
            ),
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
