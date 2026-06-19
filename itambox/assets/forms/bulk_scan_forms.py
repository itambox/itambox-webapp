"""Shared footer forms for the scanner-driven bulk check-in / disposal baskets.

These collect the *batch-wide* fields applied to every scanned asset. Per-asset
values that cannot be shared (disposal proceeds) are entered on each basket row
in the UI and posted as ``proceeds_<pk>`` — they are not declared here.
"""
import datetime

from django import forms
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Div, Fieldset, HTML

from core.managers import get_current_tenant
from organization.models import Location, AssetHolder
from assets.models import StatusLabel, AssetDisposal, Asset


def _tenant_locations():
    """Active-tenant location queryset (falls back to the scoped manager)."""
    qs = Location.objects.select_related('site').order_by('site__name', 'name')
    tenant = get_current_tenant()
    if tenant:
        qs = qs.filter(tenant=tenant)
    return qs


class AssetBulkCheckInForm(forms.Form):
    """Batch-wide check-in options applied to every scanned asset."""

    status = forms.ModelChoiceField(
        queryset=StatusLabel.objects.exclude(type='deployed').order_by('name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Status after check-in"),
        help_text=_("Leave blank to revert each asset to its pre-checkout status."),
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.none(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
        label=_("Return to location"),
        help_text=_("Optional. Leave blank to keep each asset's current location."),
    )
    checkin_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        label=_("Check-in date"),
    )
    notes = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}),
        required=False,
        label=_("Notes"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['location'].queryset = _tenant_locations()
        self.fields['checkin_date'].initial = datetime.date.today()

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True
        self.helper.layout = Layout(
            Div(
                Div('status', css_class='col-md-6'),
                Div('location', css_class='col-md-6'),
                css_class='row',
            ),
            Div(
                Div('checkin_date', css_class='col-md-6'),
                css_class='row',
            ),
            'notes',
        )


class AssetBulkDisposeForm(forms.ModelForm):
    """Batch-wide disposal options. ``proceeds`` is captured per-row in the UI."""

    disposal_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        required=True,
        label=_("Disposal date"),
    )

    class Meta:
        model = AssetDisposal
        fields = [
            'disposal_method',
            'disposal_date',
            'data_sanitization_method',
            'sanitization_certificate',
            'sanitized_by',
            'recipient',
            'currency',
            'weee_compliant',
            'notes',
        ]
        widgets = {
            'disposal_method': forms.Select(attrs={'class': 'form-select'}),
            'data_sanitization_method': forms.Select(attrs={'class': 'form-select'}),
            'sanitization_certificate': forms.TextInput(attrs={'class': 'form-control'}),
            'sanitized_by': forms.TextInput(attrs={'class': 'form-control'}),
            'recipient': forms.TextInput(attrs={'class': 'form-control'}),
            'currency': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'weee_compliant': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['disposal_date'].initial = datetime.date.today()

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True
        self.helper.layout = Layout(
            Fieldset(
                _('Disposal Details'),
                Div(
                    Div('disposal_method', css_class='col-md-6'),
                    Div('disposal_date', css_class='col-md-6'),
                    css_class='row',
                ),
                Div(
                    Div('recipient', css_class='col-md-6'),
                    Div('currency', css_class='col-md-6'),
                    css_class='row',
                ),
            ),
            Fieldset(
                _('Data Sanitization & Compliance'),
                Div(
                    Div('data_sanitization_method', css_class='col-md-4'),
                    Div('sanitized_by', css_class='col-md-4'),
                    Div('sanitization_certificate', css_class='col-md-4'),
                    css_class='row',
                ),
                Div(
                    Div('weee_compliant', css_class='col-md-4 d-flex align-items-end pb-2'),
                    css_class='row',
                ),
            ),
            'notes',
        )


def _tenant_holders():
    qs = AssetHolder.objects.all().order_by('last_name', 'first_name')
    tenant = get_current_tenant()
    if tenant:
        qs = qs.filter(tenant=tenant)
    return qs


def _tenant_target_assets():
    qs = Asset.objects.exclude(
        status__type__in=['undeployable', 'in_repair', 'on_order', 'archived']
    ).order_by('name')
    tenant = get_current_tenant()
    if tenant:
        qs = qs.filter(tenant=tenant)
    return qs


class AssetBulkCheckOutForm(forms.Form):
    """Batch-wide check-out target + options applied to every scanned asset.

    Exactly one target (holder / location / parent asset) is required; the
    submit view enforces that on the raw POST (this form renders the footer).
    """

    asset_holder = forms.ModelChoiceField(
        queryset=AssetHolder.objects.none(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
        label=_("Asset holder"),
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.none(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
        label=_("Location"),
    )
    asset_target = forms.ModelChoiceField(
        queryset=Asset.objects.none(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
        label=_("Parent asset"),
    )
    status = forms.ModelChoiceField(
        queryset=StatusLabel.objects.filter(type='deployed').order_by('name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Status after check-out"),
    )
    checkout_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        label=_("Check-out date"),
    )
    expected_checkin = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        label=_("Expected check-in date"),
    )
    notes = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}),
        required=False,
        label=_("Notes"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['asset_holder'].queryset = _tenant_holders()
        self.fields['location'].queryset = _tenant_locations()
        self.fields['asset_target'].queryset = _tenant_target_assets()
        initial_status = StatusLabel.objects.filter(type='deployed').first()
        if initial_status:
            self.fields['status'].initial = initial_status

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True
        self.helper.layout = Layout(
            HTML('<p class="text-muted small mb-2">%s</p>'
                 % _("Choose exactly one target — a holder, a location, or a parent asset.")),
            'asset_holder',
            'location',
            'asset_target',
            Div(
                Div('status', css_class='col-md-6'),
                Div('checkout_date', css_class='col-md-6'),
                css_class='row',
            ),
            Div(
                Div('expected_checkin', css_class='col-md-6'),
                css_class='row',
            ),
            'notes',
        )
