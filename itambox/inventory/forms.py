from django import forms
from django.core.exceptions import ValidationError
from django.urls import reverse
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Row, Column, Div

from core.forms import SlugModelForm, FilterForm
from extras.models import Tag
from organization.models import Location, AssetHolder, Tenant
from assets.models import Manufacturer, Category, Asset, Supplier
from .models import Accessory, Consumable, Kit, KitItem, AccessoryStock, ConsumableStock, Component, ComponentStock, ComponentAllocation


from extras.customfields import CustomFieldModelFormMixin

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


class ConsumableForm(CustomFieldModelFormMixin, SlugModelForm):
    manufacturer = forms.ModelChoiceField(
        queryset=Manufacturer.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    category = forms.ModelChoiceField(
        queryset=Category.objects.filter(applies_to__consumable=True),
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
        model = Consumable
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
        cancel_url = self.instance.get_absolute_url() if self.instance.pk else reverse('inventory:consumable_list')

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


class KitForm(forms.ModelForm):
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'data-tomselect-tags': 'true'}),
    )

    class Meta:
        model = Kit
        fields = ['name', 'description', 'tenant', 'tags']
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
        cancel_url = reverse('inventory:kit_list')
        
        self.helper.layout = Layout(
            'name',
            'description',
            'tenant',
            'tags',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )


class KitItemForm(forms.ModelForm):
    class Meta:
        model = KitItem
        fields = ['kit', 'asset_type', 'accessory', 'license', 'consumable', 'qty']
        widgets = {
            'kit': forms.Select(attrs={'class': 'form-select'}),
            'asset_type': forms.Select(attrs={'class': 'form-select'}),
            'accessory': forms.Select(attrs={'class': 'form-select'}),
            'license': forms.Select(attrs={'class': 'form-select'}),
            'consumable': forms.Select(attrs={'class': 'form-select'}),
            'qty': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        
        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = self.instance.kit.get_absolute_url() if (self.instance.pk and self.instance.kit) else reverse('inventory:kit_list')
        
        self.helper.layout = Layout(
            'kit',
            'asset_type',
            'accessory',
            'license',
            'consumable',
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
        consumable = cleaned_data.get('consumable')

        targets = [asset_type, accessory, license_item, consumable]
        filled = [t for t in targets if t is not None]
        if len(filled) == 0:
            raise ValidationError("A kit item must select either an Asset Type, Accessory, License, or Consumable.")
        if len(filled) > 1:
            raise ValidationError("A kit item cannot select more than one target (must be either Asset Type OR Accessory OR License OR Consumable).")
        return cleaned_data


class BaseCheckoutForm(forms.Form):
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
    assigned_asset = forms.ModelChoiceField(
        queryset=Asset.objects.all().order_by('asset_tag'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Assign to Asset"
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        label="Notes"
    )

    def __init__(self, *args, **kwargs):
        tenant = kwargs.pop('tenant', None)
        super().__init__(*args, **kwargs)
        if tenant:
            self.fields['assigned_holder'].queryset = AssetHolder.objects.filter(tenant=tenant).order_by('last_name', 'first_name')

    def clean(self):
        cleaned_data = super().clean()
        holder = cleaned_data.get('assigned_holder')
        location = cleaned_data.get('assigned_location')
        asset = cleaned_data.get('assigned_asset')

        filled = [t for t in [holder, location, asset] if t is not None]
        if len(filled) == 0:
            raise ValidationError("You must select either an Asset Holder, a Location, or an Asset.")
        if len(filled) > 1:
            raise ValidationError("Please select exactly one target (either Asset Holder, Location, OR Asset).")
        return cleaned_data


class KitCheckoutForm(BaseCheckoutForm):
    source_location = forms.ModelChoiceField(
        queryset=Location.objects.all().order_by('name'),
        required=True,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Source Location (stock deduction)"
    )

    def __init__(self, *args, **kwargs):
        self.kit = kwargs.pop('kit', None)
        tenant = self.kit.tenant if self.kit else None
        super().__init__(*args, tenant=tenant, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            'source_location',
            'assigned_holder',
            HTML('<p class="text-muted text-center my-2">OR</p>'),
            'assigned_location',
            'notes'
        )


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


class ConsumableStockForm(forms.ModelForm):
    class Meta:
        model = ConsumableStock
        fields = ['consumable', 'location', 'qty']
        widgets = {
            'consumable': forms.Select(attrs={'class': 'form-select'}),
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
            'consumable',
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


class ConsumableCheckoutForm(BaseCheckoutForm):
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
        self.consumable = kwargs.pop('consumable', None)
        tenant = self.consumable.tenant if self.consumable else None
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
        if self.consumable and qty:
            remaining = self.consumable.available
            if not self.consumable.allow_overallocate and qty > remaining:
                raise ValidationError(f"Cannot checkout {qty} units. Only {remaining} units are currently in stock.")
        return cleaned_data


from .filters import AccessoryFilterSet, ConsumableFilterSet, KitFilterSet, AccessoryStockFilterSet, ConsumableStockFilterSet, AccessoryAssignmentFilterSet, ConsumableAssignmentFilterSet

class AccessoryFilterForm(FilterForm):
    filterset_class = AccessoryFilterSet

class ConsumableFilterForm(FilterForm):
    filterset_class = ConsumableFilterSet

class KitFilterForm(FilterForm):
    filterset_class = KitFilterSet

class AccessoryStockFilterForm(FilterForm):
    filterset_class = AccessoryStockFilterSet

class ConsumableStockFilterForm(FilterForm):
    filterset_class = ConsumableStockFilterSet

class AccessoryAssignmentFilterForm(FilterForm):
    filterset_class = AccessoryAssignmentFilterSet

class ConsumableAssignmentFilterForm(FilterForm):
    filterset_class = ConsumableAssignmentFilterSet


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


class ConsumableStockModalForm(forms.ModelForm):
    class Meta:
        model = ConsumableStock
        fields = ['location', 'qty']
        widgets = {
            'location': forms.Select(attrs={'class': 'form-select'}),
            'qty': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False


class ComponentForm(CustomFieldModelFormMixin, SlugModelForm):
    manufacturer = forms.ModelChoiceField(
        queryset=Manufacturer.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    category = forms.ModelChoiceField(
        queryset=Category.objects.filter(applies_to__component=True),
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
        model = Component
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
        cancel_url = self.instance.get_absolute_url() if self.instance.pk else reverse('inventory:inventory_list') + '?type=components'

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
        label="Assign to Asset Holder"
    )
    assigned_location = forms.ModelChoiceField(
        queryset=Location.objects.all().select_related('site').order_by('site__name', 'name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Assign to Location"
    )
    assigned_asset = forms.ModelChoiceField(
        queryset=Asset.objects.all().order_by('asset_tag'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Assign to Asset"
    )
    from_location = forms.ModelChoiceField(
        queryset=Location.objects.all().select_related('site').order_by('name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="From Location"
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

    class Meta:
        model = ComponentAllocation
        fields = ['component', 'assigned_holder', 'assigned_location', 'assigned_asset', 'from_location', 'qty', 'notes']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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
            raise ValidationError("You must select either an Asset Holder, a Location, or an Asset.")
        if len(filled) > 1:
            raise ValidationError("Please select exactly one target (either Asset Holder, Location, OR Asset).")

        if component and qty:
            remaining = component.available_stock
            if not component.allow_overallocate and qty > remaining:
                raise ValidationError(f"Cannot checkout {qty} units. Only {remaining} units are currently in stock.")
        return cleaned_data


class ComponentCheckoutForm(BaseCheckoutForm):
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
        self.component = kwargs.pop('component', None)
        tenant = self.component.tenant if self.component else None
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
        if self.component and qty:
            remaining = self.component.available_stock
            if not self.component.allow_overallocate and qty > remaining:
                raise ValidationError(f"Cannot checkout {qty} units. Only {remaining} units are currently in stock.")
        return cleaned_data


from .filters import ComponentFilterSet, ComponentStockFilterSet, ComponentAllocationFilterSet

class ComponentFilterForm(FilterForm):
    filterset_class = ComponentFilterSet

class ComponentStockFilterForm(FilterForm):
    filterset_class = ComponentStockFilterSet

class ComponentAllocationFilterForm(FilterForm):
    filterset_class = ComponentAllocationFilterSet


class ComponentStockModalForm(forms.ModelForm):
    class Meta:
        model = ComponentStock
        fields = ['location', 'qty']
        widgets = {
            'location': forms.Select(attrs={'class': 'form-select'}),
            'qty': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False
