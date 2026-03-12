from django import forms
from django.urls import reverse
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Row, Column
from core.forms import SlugModelForm, FilterForm
from extras.models import Tag
from assets.models import Manufacturer, Asset, Category
from organization.models import Location, Tenant
from .models import Component, ComponentStock, ComponentAllocation


class ComponentForm(SlugModelForm):
    manufacturer = forms.ModelChoiceField(
        queryset=Manufacturer.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    category = forms.ModelChoiceField(
        queryset=Category.objects.filter(applies_to__component=True),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Tenant"
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'data-tomselect-tags': 'true'}),
        label="Tags"
    )

    class Meta:
        model = Component
        fields = ['manufacturer', 'name', 'slug', 'category', 'part_number', 'specs', 'min_stock_level', 'description', 'tenant', 'tags']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control', 'slugify': 'name'}),
            'part_number': forms.TextInput(attrs={'class': 'form-control'}),
            'min_stock_level': forms.NumberInput(attrs={'class': 'form-control'}),
            'specs': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }
        help_texts = {
            'slug': 'URL-friendly identifier. Leave blank to auto-generate.',
            'specs': 'JSON format: {"speed_mhz": 3200, "capacity_gb": 16, "type": "DDR4"}',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.fields['slug'].widget.attrs['slugify'] = 'name'

        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = self.instance.get_absolute_url() if self.instance.pk else reverse('components:component_list')

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
                Column('min_stock_level', css_class='col-md-6')
            ),
            Row(
                Column('tenant', css_class='col-md-6'),
                Column('tags', css_class='col-md-6')
            ),
            'specs',
            'description',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )


class ComponentStockForm(forms.ModelForm):
    component = forms.ModelChoiceField(
        queryset=Component.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.all().select_related('site'),
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = ComponentStock
        fields = ['component', 'location', 'qty']
        widgets = {
            'qty': forms.NumberInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True

        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = reverse('components:componentstock_list')

        self.helper.layout = Layout(
            Row(
                Column('component', css_class='col-md-6'),
                Column('location', css_class='col-md-6')
            ),
            'qty',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )


class ComponentAllocationForm(forms.ModelForm):
    component = forms.ModelChoiceField(
        queryset=Component.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    asset = forms.ModelChoiceField(
        queryset=Asset.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    from_location = forms.ModelChoiceField(
        queryset=Location.objects.all().select_related('site'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="From Location"
    )

    class Meta:
        model = ComponentAllocation
        fields = ['component', 'asset', 'from_location', 'qty_allocated', 'notes']
        widgets = {
            'qty_allocated': forms.NumberInput(attrs={'class': 'form-control'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True

        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = reverse('components:componentallocation_list')

        self.helper.layout = Layout(
            Row(
                Column('component', css_class='col-md-6'),
                Column('asset', css_class='col-md-6')
            ),
            Row(
                Column('from_location', css_class='col-md-6'),
                Column('qty_allocated', css_class='col-md-6'),
            ),
            'notes',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )


class ComponentFilterForm(FilterForm):
    from .filters import ComponentFilterSet
    filterset_class = ComponentFilterSet


class ComponentStockFilterForm(FilterForm):
    from .filters import ComponentStockFilterSet
    filterset_class = ComponentStockFilterSet


class ComponentAllocationFilterForm(FilterForm):
    from .filters import ComponentAllocationFilterSet
    filterset_class = ComponentAllocationFilterSet
