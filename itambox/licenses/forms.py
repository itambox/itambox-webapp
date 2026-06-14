from django import forms
from django.utils.translation import gettext_lazy as _
from software.models import Software
from extras.models import Tag
from core.forms import FilterForm
from .filters import LicenseFilterSet
from .models import License, LicenseSeatAssignment
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Div
from django.urls import reverse
from assets.models import Asset
from organization.models import AssetHolder

from extras.customfields import CustomFieldModelFormMixin

class LicenseForm(CustomFieldModelFormMixin, forms.ModelForm):
    """Form for creating and updating License entitlements."""
    software = forms.ModelChoiceField(
        queryset=Software.objects.all(),
        label=_("Software Catalog Item")
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select'})
    )

    class Meta:
        model = License
        fields = ('name', 'software', 'license_type', 'product_key', 'seats', 'purchase_date', 'purchase_cost', 'currency', 'order_number', 'version', 'subscription', 'expiration_date', 'notes', 'tags', 'tenant')
        widgets = {
            'product_key': forms.Textarea(attrs={'rows': 2}),
            'purchase_date': forms.DateInput(attrs={'type': 'date'}),
            'expiration_date': forms.DateInput(attrs={'type': 'date'}),
            'subscription': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }
        help_texts = {
            'name': _("Unique renewal or purchase name (e.g., Office 365 E5 Enterprise Renewal FY26)"),
            'seats': _("Total number of activation seats purchased"),
            'product_key': _("License activation key or volume credential"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['product_key'].initial = self.instance.decrypted_product_key
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True

        if self.instance and self.instance.pk:
            button_text = _('Update')
            cancel_url = self.instance.get_absolute_url()
        else:
            button_text = _('Create')
            cancel_url = reverse('licenses:license_list')

        self.helper.layout = Layout(
            Div(
                'name',
                'software',
                'license_type',
                'product_key',
                'seats',
                'purchase_date',
                'purchase_cost',
                'currency',
                'order_number',
                'version',
                'subscription',
                'expiration_date',
                'tenant',
                'notes',
                'tags',
                css_class='mb-3'
            ),
            HTML('<div class="mt-3 d-flex justify-content-between">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary">{_("Cancel")}</a>'),
            HTML('</div>')
        )
        self.append_custom_fields_to_layout()

class LicenseFilterForm(FilterForm):
    filterset_class = LicenseFilterSet


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
