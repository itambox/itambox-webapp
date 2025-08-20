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
