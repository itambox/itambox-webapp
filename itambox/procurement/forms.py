from django import forms
from django.core.exceptions import ValidationError
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, Row, Column
from .models import PurchaseOrder, PurchaseOrderLine, Contract

class PurchaseOrderForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrder
        fields = ['order_number', 'supplier', 'currency', 'order_date', 'expected_delivery_date', 'destination_location', 'tenant', 'notes']
        widgets = {
            'order_date': forms.DateInput(attrs={'type': 'date'}),
            'expected_delivery_date': forms.DateInput(attrs={'type': 'date'}),
            'supplier': forms.Select(attrs={'data-tom-select': ''}),
            'destination_location': forms.Select(attrs={'data-tom-select': ''}),
            'tenant': forms.Select(attrs={'data-tom-select': ''}),
            'currency': forms.Select(attrs={'data-tom-select': ''}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.layout = Layout(
            Row(
                Column('order_number', css_class='form-group col-md-6 mb-0'),
                Column('supplier', css_class='form-group col-md-6 mb-0'),
                css_class='form-row'
            ),
            Row(
                Column('currency', css_class='form-group col-md-6 mb-0'),
                Column('tenant', css_class='form-group col-md-6 mb-0'),
                css_class='form-row'
            ),
            Row(
                Column('order_date', css_class='form-group col-md-6 mb-0'),
                Column('expected_delivery_date', css_class='form-group col-md-6 mb-0'),
                css_class='form-row'
            ),
            Row(
                Column('destination_location', css_class='form-group col-md-6 mb-0'),
                css_class='form-row'
            ),
            'notes',
            Submit('submit', 'Save Purchase Order', css_class='btn btn-primary mt-3')
        )

class PurchaseOrderLineForm(forms.ModelForm):
    item_category = forms.ChoiceField(
        choices=[
            ('', '---------'),
            ('asset_type', 'Asset Type'),
            ('component', 'Component'),
            ('accessory', 'Accessory'),
            ('consumable', 'Consumable'),
            ('license', 'License'),
        ],
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_request_category'}),
        label="Item Category",
    )

    class Meta:
        model = PurchaseOrderLine
        fields = ['item_category', 'asset_type', 'component', 'accessory', 'consumable', 'license', 'qty_ordered', 'unit_price']
        widgets = {
            'asset_type': forms.Select(attrs={'data-tom-select': ''}),
            'component': forms.Select(attrs={'data-tom-select': ''}),
            'accessory': forms.Select(attrs={'data-tom-select': ''}),
            'consumable': forms.Select(attrs={'data-tom-select': ''}),
            'license': forms.Select(attrs={'data-tom-select': ''}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Populate initial value for item_category if editing
        if self.instance and self.instance.pk:
            if self.instance.asset_type:
                self.fields['item_category'].initial = 'asset_type'
            elif self.instance.component:
                self.fields['item_category'].initial = 'component'
            elif self.instance.accessory:
                self.fields['item_category'].initial = 'accessory'
            elif self.instance.consumable:
                self.fields['item_category'].initial = 'consumable'
            elif self.instance.license:
                self.fields['item_category'].initial = 'license'

        # Scoping querysets by tenant
        from core.managers import get_current_tenant
        tenant = get_current_tenant()
        if tenant:
            self.fields['component'].queryset = self.fields['component'].queryset.filter(tenant=tenant)
            self.fields['accessory'].queryset = self.fields['accessory'].queryset.filter(tenant=tenant)
            self.fields['consumable'].queryset = self.fields['consumable'].queryset.filter(tenant=tenant)
            self.fields['license'].queryset = self.fields['license'].queryset.filter(tenant=tenant)

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            'item_category',
            'asset_type',
            'component',
            'accessory',
            'consumable',
            'license',
            Row(
                Column('qty_ordered', css_class='form-group col-md-6 mb-0'),
                Column('unit_price', css_class='form-group col-md-6 mb-0'),
                css_class='form-row'
            ),
            Submit('submit', 'Save Line Item', css_class='btn btn-primary mt-3')
        )

    def clean(self):
        cleaned_data = super().clean()
        item_category = cleaned_data.get('item_category')
        
        if not item_category:
            raise ValidationError("Please select an Item Category.")
            
        fields_map = {
            'asset_type': 'asset_type',
            'component': 'component',
            'accessory': 'accessory',
            'consumable': 'consumable',
            'license': 'license',
        }
        
        target_field = fields_map.get(item_category)
        if not target_field:
            raise ValidationError(f"Invalid category selected: {item_category}")
            
        if not cleaned_data.get(target_field):
            field_label = self.fields[target_field].label or target_field.replace('_', ' ').title()
            raise ValidationError({target_field: f"Please select a {field_label}."})
            
        # Clear all other fields to prevent multiple fields from being saved
        for cat, field_name in fields_map.items():
            if field_name != target_field:
                cleaned_data[field_name] = None
                
        return cleaned_data


class ContractForm(forms.ModelForm):
    class Meta:
        model = Contract
        fields = [
            'name', 'contract_number', 'contract_type', 'status',
            'supplier', 'cost', 'currency', 'billing_cycle',
            'start_date', 'end_date', 'renewal_date', 'auto_renew',
            'sla_response_time', 'sla_resolution_time', 'coverage_hours', 'sla_terms',
            'assets', 'purchase_order', 'tenant', 'notes',
        ]
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
            'renewal_date': forms.DateInput(attrs={'type': 'date'}),
            'supplier': forms.Select(attrs={'data-tom-select': ''}),
            'purchase_order': forms.Select(attrs={'data-tom-select': ''}),
            'tenant': forms.Select(attrs={'data-tom-select': ''}),
            'currency': forms.Select(attrs={'data-tom-select': ''}),
            'contract_type': forms.Select(attrs={'class': 'form-select'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'billing_cycle': forms.Select(attrs={'class': 'form-select'}),
            'assets': forms.SelectMultiple(attrs={'data-tom-select': ''}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.layout = Layout(
            Row(
                Column('name', css_class='form-group col-md-8 mb-0'),
                Column('contract_number', css_class='form-group col-md-4 mb-0'),
                css_class='form-row'
            ),
            Row(
                Column('contract_type', css_class='form-group col-md-4 mb-0'),
                Column('status', css_class='form-group col-md-4 mb-0'),
                Column('tenant', css_class='form-group col-md-4 mb-0'),
                css_class='form-row'
            ),
            Row(
                Column('supplier', css_class='form-group col-md-6 mb-0'),
                Column('purchase_order', css_class='form-group col-md-6 mb-0'),
                css_class='form-row'
            ),
            Row(
                Column('cost', css_class='form-group col-md-4 mb-0'),
                Column('currency', css_class='form-group col-md-4 mb-0'),
                Column('billing_cycle', css_class='form-group col-md-4 mb-0'),
                css_class='form-row'
            ),
            Row(
                Column('start_date', css_class='form-group col-md-4 mb-0'),
                Column('end_date', css_class='form-group col-md-4 mb-0'),
                Column('renewal_date', css_class='form-group col-md-4 mb-0'),
                css_class='form-row'
            ),
            Row(
                Column('auto_renew', css_class='form-group col-md-12 mb-0'),
                css_class='form-row'
            ),
            Row(
                Column('sla_response_time', css_class='form-group col-md-4 mb-0'),
                Column('sla_resolution_time', css_class='form-group col-md-4 mb-0'),
                Column('coverage_hours', css_class='form-group col-md-4 mb-0'),
                css_class='form-row'
            ),
            'sla_terms',
            'assets',
            'notes',
            Submit('submit', 'Save Contract', css_class='btn btn-primary mt-3')
        )


from django.core.exceptions import ValidationError

class ReceiveLineForm(forms.Form):
    line_id = forms.IntegerField(widget=forms.HiddenInput)
    qty_to_receive = forms.IntegerField(
        min_value=0,
        label="Qty to Receive",
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': 0})
    )

class BaseReceiveLineFormSet(forms.BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        total_qty = 0
        for form in self.forms:
            total_qty += form.cleaned_data.get('qty_to_receive', 0)
        if total_qty == 0:
            raise ValidationError("You must specify at least one item to receive.")

ReceiveLineFormSet = forms.formset_factory(
    ReceiveLineForm, formset=BaseReceiveLineFormSet, extra=0
)

class AssetProvisionForm(forms.Form):
    line_id = forms.IntegerField(widget=forms.HiddenInput)
    serial_number = forms.CharField(
        max_length=100,
        required=False,
        label="Serial Number",
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional'})
    )
    asset_tag = forms.CharField(
        max_length=50,
        required=False,
        label="Asset Tag",
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Auto-generate'})
    )
    name = forms.CharField(
        max_length=255,
        required=False,
        label="Asset Name",
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Model Name'})
    )

class BaseAssetProvisionFormSet(forms.BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        
        tags = set()
        serials = set()
        from assets.models import Asset
        
        for form in self.forms:
            if not form.is_valid():
                continue
            
            tag = form.cleaned_data.get('asset_tag')
            serial = form.cleaned_data.get('serial_number')
            
            if tag:
                tag = tag.strip()
                if tag in tags:
                    form.add_error('asset_tag', "Duplicate asset tag in this batch.")
                tags.add(tag)
                # Check DB for duplicate tag
                if Asset.objects.filter(asset_tag=tag).exists():
                    form.add_error('asset_tag', f"An asset with tag '{tag}' already exists.")
            
            if serial:
                serial = serial.strip()
                if serial in serials:
                    form.add_error('serial_number', "Duplicate serial number in this batch.")
                serials.add(serial)
                # Check DB for duplicate serial
                if Asset.objects.filter(serial_number=serial).exists():
                    form.add_error('serial_number', f"An asset with serial number '{serial}' already exists.")

AssetProvisionFormSet = forms.formset_factory(
    AssetProvisionForm, formset=BaseAssetProvisionFormSet, extra=0
)
