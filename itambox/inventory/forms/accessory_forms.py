from django import forms
from django.core.exceptions import ValidationError
from django.urls import reverse
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Row, Column

from core.forms import SlugModelForm, FilterForm
from extras.models import Tag
from extras.customfields import CustomFieldModelFormMixin
from organization.models import Location
from assets.models import Manufacturer, Category, Supplier
from ..models import Accessory, AccessoryStock
from ..filters import AccessoryFilterSet, AccessoryStockFilterSet, AccessoryAssignmentFilterSet
from .base_forms import BaseCheckoutForm


class AccessoryForm(CustomFieldModelFormMixin, SlugModelForm):
    manufacturer = forms.ModelChoiceField(
        queryset=Manufacturer.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    category = forms.ModelChoiceField(
        queryset=Category.objects.filter(applies_to__accessory=True),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Category"
    )
    supplier = forms.ModelChoiceField(
        queryset=Supplier.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
        label="Supplier"
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'data-tomselect-tags': 'true'}),
        label="Tags"
    )

    class Meta:
        model = Accessory
        fields = ['manufacturer', 'name', 'slug', 'category', 'supplier', 'part_number', 'min_qty', 'allow_overallocate', 'notes', 'tags', 'tenant']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control', 'slugify': 'name'}),
            'part_number': forms.TextInput(attrs={'class': 'form-control'}),
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
        cancel_url = self.instance.get_absolute_url() if self.instance.pk else reverse('inventory:accessory_list')

        self.helper.layout = Layout(
            Row(
                Column('manufacturer', css_class='col-md-6'),
                Column('name', css_class='col-md-6')
            ),
            Row(
                Column('supplier', css_class='col-md-6'),
                Column('part_number', css_class='col-md-6')
            ),
            Row(
                Column('slug', css_class='col-md-6'),
                Column('category', css_class='col-md-6')
            ),
            Row(
                Column('min_qty', css_class='col-md-6'),
                Column('tenant', css_class='col-md-6')
            ),
            Row(
                Column('allow_overallocate', css_class='col-md-6')
            ),
            Row(
                Column('tags', css_class='col-md-12')
            ),
            'notes',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )
        self.append_custom_fields_to_layout()


class AccessoryStockForm(forms.ModelForm):
    class Meta:
        model = AccessoryStock
        fields = ['accessory', 'location', 'qty']
        widgets = {
            'accessory': forms.Select(attrs={'class': 'form-select'}),
            'location': forms.Select(attrs={'class': 'form-select'}),
            'qty': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        button_text = 'Update' if self.instance.pk else 'Create'
        self.helper.layout = Layout(
            'accessory',
            'location',
            'qty',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML('</div>')
        )


class AccessoryCheckoutForm(BaseCheckoutForm):
    qty = forms.IntegerField(
        initial=1,
        min_value=1,
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
        label="Quantity"
    )
    from_location = forms.ModelChoiceField(
        queryset=Location.objects.all().order_by('name'),
        required=True,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="From Location"
    )

    def __init__(self, *args, **kwargs):
        self.accessory = kwargs.pop('accessory', None)
        tenant = self.accessory.tenant if self.accessory else None
        super().__init__(*args, tenant=tenant, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            'from_location',
            'assigned_holder',
            HTML('<p class="text-muted text-center my-2">OR</p>'),
            'assigned_location',
            HTML('<p class="text-muted text-center my-2">OR</p>'),
            'assigned_asset',
            'qty',
            'notes'
        )

    def clean(self):
        cleaned_data = super().clean()
        qty = cleaned_data.get('qty')
        if self.accessory and qty:
            remaining = self.accessory.available
            if not self.accessory.allow_overallocate and qty > remaining:
                raise ValidationError(f"Cannot checkout {qty} units. Only {remaining} units are currently in stock.")
        return cleaned_data


class AccessoryFilterForm(FilterForm):
    filterset_class = AccessoryFilterSet


class AccessoryStockFilterForm(FilterForm):
    filterset_class = AccessoryStockFilterSet


class AccessoryAssignmentFilterForm(FilterForm):
    filterset_class = AccessoryAssignmentFilterSet


class AccessoryStockModalForm(forms.ModelForm):
    class Meta:
        model = AccessoryStock
        fields = ['location', 'qty']
        widgets = {
            'location': forms.Select(attrs={'class': 'form-select'}),
            'qty': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False
