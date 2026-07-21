from django import forms
from django.utils.translation import gettext_lazy as _
from software.models import Software
from extras.models import Tag
from core.forms import FilterForm, CrispyFormMixin, scope_tenant_field
from .filters import LicenseFilterSet
from .models import License, LicenseSeatAssignment
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Fieldset, Div, Row, Column
from crispy_forms.layout import HTML, Submit
from django.urls import reverse
from django.utils.html import format_html
from assets.models import Asset, Supplier
from organization.models import AssetHolder

from extras.customfields import CustomFieldModelFormMixin

class LicenseForm(CrispyFormMixin, CustomFieldModelFormMixin, forms.ModelForm):
    """Form for creating and updating License entitlements."""
    software = forms.ModelChoiceField(
        queryset=Software.objects.all(),
        label=_("Software Catalog Item"),
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
    )
    supplier = forms.ModelChoiceField(
        queryset=Supplier.objects.all(),
        required=False,
        label=_("Supplier"),
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''}),
    )

    class Meta:
        model = License
        fields = ('name', 'license_type', 'software', 'version', 'seats', 'product_key', 'order_number', 'supplier', 'purchase_date', 'purchase_cost', 'currency', 'subscription', 'cost_center', 'expiration_date', 'tenant', 'notes', 'tags')
        widgets = {
            'product_key': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'purchase_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'expiration_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'subscription': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'cost_center': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'tenant': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'license_type': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'version': forms.TextInput(attrs={'class': 'form-control'}),
            'seats': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'order_number': forms.TextInput(attrs={'class': 'form-control'}),
            'purchase_cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'currency': forms.TextInput(attrs={'class': 'form-control', 'maxlength': 3}),
        }
        help_texts = {
            'name': _("Unique renewal or purchase name (e.g., Office 365 E5 Enterprise Renewal FY26)"),
            'seats': _("Total number of activation seats purchased"),
            'product_key': _("License activation key or volume credential"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        scope_tenant_field(self)
        # Rescope the tenant-owned `cost_center`/`subscription` FK pickers per
        # request (import-frozen unscoped). `software` is validated same-tenant in
        # License.clean(); `supplier` is a global catalogue model.
        for fk_name in ('cost_center', 'subscription'):
            field = self.fields.get(fk_name)
            if field is not None and getattr(field, 'queryset', None) is not None:
                field.queryset = field.queryset.model._default_manager.all()
        if self.instance and self.instance.pk:
            self.fields['product_key'].initial = self.instance.decrypted_product_key

        if self.instance and self.instance.pk:
            cancel_url = self.instance.get_absolute_url()
        else:
            cancel_url = reverse('licenses:license_list')

        self.helper.layout = Layout(
            Fieldset(
                _('Identity'),
                Div(
                    Div('name', css_class='col-md-6'),
                    Div('license_type', css_class='col-md-6'),
                    css_class='row',
                ),
                Div(
                    Div('software', css_class='col-md-6'),
                    Div('version', css_class='col-md-6'),
                    css_class='row',
                ),
            ),
            Fieldset(
                _('Entitlement'),
                Div(
                    Div('seats', css_class='col-md-6'),
                    Div('product_key', css_class='col-md-6'),
                    css_class='row',
                ),
            ),
            Fieldset(
                _('Procurement & Financial'),
                Div(
                    Div('order_number', css_class='col-md-4'),
                    Div('supplier', css_class='col-md-4'),
                    Div('purchase_date', css_class='col-md-4'),
                    css_class='row',
                ),
                Div(
                    Div('purchase_cost', css_class='col-md-4'),
                    Div('currency', css_class='col-md-4'),
                    css_class='row',
                ),
            ),
            Fieldset(
                _('Funding'),
                Div(
                    Div('subscription', css_class='col-md-6'),
                    Div('cost_center', css_class='col-md-6'),
                    css_class='row',
                ),
            ),
            Fieldset(
                _('Lifecycle'),
                Div(
                    Div('expiration_date', css_class='col-md-6'),
                    css_class='row',
                ),
            ),
            Fieldset(
                _('Scope'),
                Div(
                    Div('tenant', css_class='col-md-6'),
                    css_class='row',
                ),
            ),
            Fieldset(
                _('Notes & Tags'),
                'notes',
                'tags',
            ),
            *self.action_buttons(cancel_url),
        )
        self.append_custom_fields_to_layout()

class LicenseFilterForm(FilterForm):
    filterset_class = LicenseFilterSet


class LicenseSeatAssignmentForm(forms.ModelForm):
    """Assign a license seat to an asset (asset-scoped quick-add)."""

    class Meta:
        model = LicenseSeatAssignment
        fields = ['license', 'asset', 'notes']
        widgets = {
            'license': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'asset': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True

        button_text = _('Assign License')
        try:
            cancel_url = reverse('licenses:license_list')
        except Exception:
            cancel_url = '/'

        self.helper.layout = Layout(
            'asset',
            'license',
            'notes',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(format_html(
                '<a href="{}" class="btn btn-outline-secondary ms-2" data-no-dirty-track="true">Cancel</a>',
                cancel_url,
            )),
            HTML('</div>'),
        )

    def clean(self):
        cleaned = super().clean()
        lic = cleaned.get('license')
        asset = cleaned.get('asset')
        if lic and asset and not self.instance.pk:
            # Friendly pre-checks; the model's DB constraints enforce these too.
            if LicenseSeatAssignment.objects.filter(license=lic, asset=asset).exists():
                raise forms.ValidationError(_("This asset already holds a seat on this license."))
            if lic.available_seats <= 0:
                raise forms.ValidationError(_("No seats available on this license."))
        return cleaned


class LicenseCheckOutForm(forms.Form):
    TARGET_CHOICES = [
        ('holder', _('Asset Holder')),
        ('asset', _('Hardware Asset')),
    ]

    target_type = forms.ChoiceField(
        choices=TARGET_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Assign to")
    )
    assigned_holder = forms.ModelChoiceField(
        queryset=AssetHolder.objects.all().order_by('last_name', 'first_name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Asset Holder")
    )
    asset = forms.ModelChoiceField(
        queryset=Asset.objects.exclude(status__type='undeployable').order_by('name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Hardware Asset")
    )
    notes = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
        required=False,
        label=_("Notes")
    )

    def clean(self):
        cleaned_data = super().clean()
        target_type = cleaned_data.get('target_type')
        holder = cleaned_data.get('assigned_holder')
        asset = cleaned_data.get('asset')

        if target_type == 'holder' and not holder:
            raise forms.ValidationError(_("Must select an Asset Holder."), code='holder_required')
        if target_type == 'asset' and not asset:
            raise forms.ValidationError(_("Must select a Hardware Asset."), code='asset_required')
        if not target_type:
            raise forms.ValidationError(_("Must select a target type."), code='target_type_required')
        return cleaned_data

    def __init__(self, *args, **kwargs):
        license_obj = kwargs.pop('license', None)
        super().__init__(*args, **kwargs)

        # If tenant is restricted, filter candidates
        if license_obj and license_obj.tenant:
            self.fields['assigned_holder'].queryset = AssetHolder.objects.filter(tenant=license_obj.tenant).order_by('last_name', 'first_name')
            self.fields['asset'].queryset = Asset.objects.filter(tenant=license_obj.tenant).exclude(status__type='undeployable').order_by('name')

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            'target_type',
            'assigned_holder',
            'asset',
            'notes',
        )
