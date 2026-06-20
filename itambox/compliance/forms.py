from django import forms
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Row, Column, Fieldset
from core.forms import FilterForm, scope_tenant_field, scope_tenant_group_field
from assets.models import Asset, Supplier, Category, AssetMaintenance

class AssetMaintenanceFilterForm(FilterForm):
    from .filters import AssetMaintenanceFilterSet
    filterset_class = AssetMaintenanceFilterSet

class AssetMaintenanceForm(forms.ModelForm):
    asset = forms.ModelChoiceField(
        queryset=Asset.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Asset")
    )
    supplier = forms.ModelChoiceField(
        queryset=Supplier.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        required=False,
        label=_("Supplier")
    )
    maintenance_type = forms.ChoiceField(
        choices=AssetMaintenance.MAINTENANCE_TYPE_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Maintenance Type")
    )
    status = forms.ChoiceField(
        choices=AssetMaintenance._meta.get_field('status').choices,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Status")
    )
    start_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        label=_("Start Date")
    )
    completion_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        required=False,
        label=_("Completion Date")
    )

    class Meta:
        model = AssetMaintenance
        fields = [
            'asset', 'supplier', 'maintenance_type', 'status',
            'cost', 'currency', 'start_date', 'completion_date', 'performed_by',
            'description', 'notes', 'tags'
        ]
        widgets = {
            'performed_by': forms.TextInput(attrs={'class': 'form-control'}),
            'cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'currency': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'tags': forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Rescope the tenant-owned `asset` FK per request — its queryset is frozen
        # unscoped at import, so a maintenance record could otherwise reference (and
        # expose in the dropdown) another tenant's asset.
        self.fields['asset'].queryset = Asset.objects.all()

        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True

        button_text = _('Update') if self.instance and self.instance.pk else _('Create')
        cancel_url = reverse('assets:assetmaintenance_list')

        self.helper.layout = Layout(
            Row(
                Column('asset', css_class='col-md-12')
            ),
            Row(
                Column('supplier', css_class='col-md-6'),
                Column('performed_by', css_class='col-md-6')
            ),
            Row(
                Column('maintenance_type', css_class='col-md-6'),
                Column('status', css_class='col-md-6')
            ),
            Row(
                Column('cost', css_class='col-md-6'),
                Column('currency', css_class='col-md-6'),
            ),
            Row(
                Column('start_date', css_class='col-md-6'),
                Column('completion_date', css_class='col-md-6')
            ),
            'description',
            'notes',
            'tags',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">{_("Cancel")}</a>'),
            HTML('</div>')
        )


from organization.models import Tenant, TenantGroup
from extras.models import Tag
from .models import CustodyTemplate
from compliance.registry import signature_providers

class CustodyTemplateForm(forms.ModelForm):
    tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Tenant")
    )
    tenant_group = forms.ModelChoiceField(
        queryset=TenantGroup.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Tenant Group")
    )
    category = forms.ModelChoiceField(
        queryset=Category.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Target Category")
    )
    signature_provider = forms.ChoiceField(
        choices=[],
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Signature Provider")
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''}),
        label=_("Tags")
    )

    class Meta:
        model = CustodyTemplate
        fields = [
            'tenant', 'tenant_group', 'name', 'category', 'signature_provider', 'logo',
            'eula_text', 'disclaimer', 'qms_reference', 'require_acceptance',
            'email_signature_request', 'is_active', 'tags'
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'logo': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'eula_text': forms.Textarea(attrs={'class': 'form-control', 'rows': 5}),
            'disclaimer': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'qms_reference': forms.TextInput(attrs={'class': 'form-control'}),
            'require_acceptance': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'email_signature_request': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        scope_tenant_field(self)
        scope_tenant_group_field(self)
        self.fields['signature_provider'].choices = signature_providers.choices()
        
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True

        button_text = _('Update') if self.instance and self.instance.pk else _('Create')
        cancel_url = reverse('compliance:custodytemplate_list')

        self.helper.layout = Layout(
            Row(
                Column('tenant', css_class='col-md-4'),
                Column('tenant_group', css_class='col-md-4'),
                Column('name', css_class='col-md-4'),
                css_class='row g-3',
            ),
            Row(
                Column('category', css_class='col-md-6'),
                Column('signature_provider', css_class='col-md-6'),
                css_class='row g-3',
            ),
            Row(
                Column('qms_reference', css_class='col-md-6'),
                Column('is_active', css_class='col-md-6 mt-2'),
                css_class='row g-3',
            ),
            'logo',
            Fieldset(
                _('Content'),
                'eula_text',
                'disclaimer',
            ),
            Row(
                Column('require_acceptance', css_class='col-md-6 mt-2'),
                Column('email_signature_request', css_class='col-md-6 mt-2'),
                css_class='row g-3',
            ),
            'tags',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">{_("Cancel")}</a>'),
            HTML('</div>')
        )

    def clean(self):
        cleaned_data = super().clean()
        tenant = cleaned_data.get('tenant')
        tenant_group = cleaned_data.get('tenant_group')

        if tenant and tenant_group:
            raise forms.ValidationError(_("You can assign this template to either a Tenant or a Tenant Group, but not both."))

        from django.conf import settings
        if not getattr(settings, 'ALLOW_GLOBAL_CUSTODY_TEMPLATES', True):
            if not tenant and not tenant_group:
                raise forms.ValidationError(_("Global custody templates are disabled. You must select either a Tenant or a Tenant Group."))

        return cleaned_data
