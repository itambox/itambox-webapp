from django import forms
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Row, Column

from core.forms import SlugModelForm, FilterForm, scope_tenant_field
from extras.models import Tag
from extras.customfields import CustomFieldModelFormMixin
from organization.models import Location, AssetHolder
from assets.models import Manufacturer, Category, Asset, Supplier
from ..models import Component, ComponentStock, ComponentAllocation
from ..filters import ComponentFilterSet, ComponentStockFilterSet, ComponentAllocationFilterSet
from .base_forms import BaseCheckoutForm


class ComponentForm(CustomFieldModelFormMixin, SlugModelForm):
    manufacturer = forms.ModelChoiceField(
        queryset=Manufacturer.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    category = forms.ModelChoiceField(
        queryset=Category.objects.filter(applies_to__component=True),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Category")
    )
    supplier = forms.ModelChoiceField(
        queryset=Supplier.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
        label=_("Supplier")
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'data-tomselect-tags': 'true'}),
        label=_("Tags")
    )

    class Meta:
        model = Component
        fields = ['manufacturer', 'name', 'slug', 'category', 'supplier', 'part_number', 'ean', 'min_qty', 'allow_overallocate', 'notes', 'tags', 'tenant']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control', 'slugify': 'name'}),
            'part_number': forms.TextInput(attrs={'class': 'form-control'}),
            'ean': forms.TextInput(attrs={'class': 'form-control', 'inputmode': 'numeric'}),
            'min_qty': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'allow_overallocate': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        scope_tenant_field(self)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.fields['slug'].widget.attrs['slugify'] = 'name'

        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = self.instance.get_absolute_url() if self.instance.pk else reverse('inventory:inventory_list') + '?type=components'

        self.helper.layout = Layout(
            Row(
                Column('manufacturer', css_class='col-md-6'),
                Column('name', css_class='col-md-6')
            ),
            Row(
                Column('supplier', css_class='col-md-4'),
                Column('part_number', css_class='col-md-4'),
                Column('ean', css_class='col-md-4')
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
            'qty': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Rescope tenant-owned FK querysets per request (import-frozen unscoped).
        self.fields['component'].queryset = Component.objects.all()
        self.fields['location'].queryset = Location.objects.all().select_related('site')
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = reverse('inventory:inventory_list') + '?type=components'

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
    assigned_holder = forms.ModelChoiceField(
        queryset=AssetHolder.objects.all().order_by('last_name', 'first_name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Assign to Asset Holder")
    )
    assigned_location = forms.ModelChoiceField(
        queryset=Location.objects.all().select_related('site').order_by('site__name', 'name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Assign to Location")
    )
    assigned_asset = forms.ModelChoiceField(
        queryset=Asset.objects.all().order_by('asset_tag'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Assign to Asset")
    )
    from_location = forms.ModelChoiceField(
        queryset=Location.objects.all().select_related('site').order_by('name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("From Location")
    )
    qty = forms.IntegerField(
        initial=1,
        min_value=1,
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
        label=_("Quantity")
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        label=_("Notes")
    )

    class Meta:
        model = ComponentAllocation
        fields = ['component', 'assigned_holder', 'assigned_location', 'assigned_asset', 'from_location', 'qty', 'notes']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Rescope every tenant-owned FK queryset per request (import-frozen unscoped).
        self.fields['component'].queryset = Component.objects.all()
        self.fields['assigned_holder'].queryset = AssetHolder.objects.all().order_by('last_name', 'first_name')
        self.fields['assigned_location'].queryset = Location.objects.all().select_related('site').order_by('site__name', 'name')
        self.fields['assigned_asset'].queryset = Asset.objects.all().order_by('asset_tag')
        self.fields['from_location'].queryset = Location.objects.all().select_related('site').order_by('name')
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = reverse('inventory:inventory_list') + '?type=components'

        self.helper.layout = Layout(
            Row(
                Column('component', css_class='col-md-6'),
                Column('from_location', css_class='col-md-6')
            ),
            Row(
                Column('assigned_holder', css_class='col-md-4'),
                Column('assigned_location', css_class='col-md-4'),
                Column('assigned_asset', css_class='col-md-4')
            ),
            Row(
                Column('qty', css_class='col-md-6'),
            ),
            'notes',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )

    def clean(self):
        cleaned_data = super().clean()
        holder = cleaned_data.get('assigned_holder')
        location = cleaned_data.get('assigned_location')
        asset = cleaned_data.get('assigned_asset')
        qty = cleaned_data.get('qty')
        component = cleaned_data.get('component')

        filled = [t for t in [holder, location, asset] if t is not None]
        if len(filled) == 0:
            raise ValidationError(_("You must select either an Asset Holder, a Location, or an Asset."))
        if len(filled) > 1:
            raise ValidationError(_("Please select exactly one target (either Asset Holder, Location, OR Asset)."))

        if component and qty:
            remaining = component.available_stock
            if not component.allow_overallocate and qty > remaining:
                raise ValidationError(_("Cannot checkout %(qty)s units. Only %(remaining)s units are currently in stock.") % {"qty": qty, "remaining": remaining})
        return cleaned_data


class ComponentCheckoutForm(BaseCheckoutForm):
    qty = forms.IntegerField(
        initial=1,
        min_value=1,
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
        label=_("Quantity")
    )
    from_location = forms.ModelChoiceField(
        queryset=Location.objects.all().order_by('name'),
        required=True,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("From Location")
    )

    def __init__(self, *args, **kwargs):
        self.component = kwargs.pop('component', None)
        tenant = self.component.tenant if self.component else None
        # inline import: sibling-module import at call time avoids a forms-package cycle
        from ..models import ComponentStock
        super().__init__(*args, tenant=tenant, item=self.component,
                         stock_model=ComponentStock, **kwargs)
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
        if self.component and qty:
            remaining = self.component.available_stock
            if not self.component.allow_overallocate and qty > remaining:
                raise ValidationError(_("Cannot checkout %(qty)s units. Only %(remaining)s units are currently in stock.") % {"qty": qty, "remaining": remaining})
        return cleaned_data


class ComponentFilterForm(FilterForm):
    filterset_class = ComponentFilterSet


class ComponentStockFilterForm(FilterForm):
    filterset_class = ComponentStockFilterSet


class ComponentAllocationFilterForm(FilterForm):
    filterset_class = ComponentAllocationFilterSet


class ComponentStockModalForm(forms.ModelForm):
    qty = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
    )

    class Meta:
        model = ComponentStock
        fields = ['location', 'qty']
        widgets = {
            'location': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Rescope the tenant-owned `location` FK per request (import-frozen unscoped).
        self.fields['location'].queryset = Location.objects.all().select_related('site')
        self.helper = FormHelper()
        self.helper.form_tag = False
