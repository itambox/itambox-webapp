from django import forms
from django.urls import reverse
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Row, Column
from core.forms import SlugModelForm, FilterForm
from extras.models import Tag
from assets.models import Manufacturer, Asset
from .models import ComponentType, ComponentInstance

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


class ComponentTypeFilterForm(FilterForm):
    from .filters import ComponentTypeFilterSet
    filterset_class = ComponentTypeFilterSet


class ComponentInstanceFilterForm(FilterForm):
    from .filters import ComponentInstanceFilterSet
    filterset_class = ComponentInstanceFilterSet

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
    from .filters import ComponentTypeFilterSet
    filterset_class = ComponentTypeFilterSet


class ComponentInstanceFilterForm(FilterForm):
    from .filters import ComponentInstanceFilterSet
    filterset_class = ComponentInstanceFilterSet
