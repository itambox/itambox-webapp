from django import forms
from django.utils.translation import gettext_lazy as _
from assets.models import StatusLabel
from compliance.models import AssetAudit, AuditSession
from organization.models import Location, Tenant


class AssetAuditForm(forms.ModelForm):
    class Meta:
        model = AssetAudit
        fields = ['location', 'status', 'notes', 'verification_method']
        widgets = {
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'verification_method': forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        asset = kwargs.pop('asset', None)
        super().__init__(*args, **kwargs)
        self.fields['location'] = forms.ModelChoiceField(
            queryset=Location.objects.all(),
            required=True,
            widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''})
        )
        self.fields['status'] = forms.ModelChoiceField(
            queryset=StatusLabel.objects.exclude(type=StatusLabel.TYPE_ARCHIVED),
            required=True,
            widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''})
        )

        if asset:
            self.fields['location'].initial = asset.location
            self.fields['status'].initial = asset.status


class AuditSessionForm(forms.ModelForm):
    start_immediately = forms.BooleanField(
        required=False,
        initial=True,
        label=_("Start immediately"),
        help_text=_("Uncheck to save as a planned campaign and activate later."),
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    class Meta:
        model = AuditSession
        fields = ['name', 'tenant', 'location']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        request = kwargs.pop('request', None)
        super().__init__(*args, **kwargs)

        # Restrict tenant choices to the current user's memberships.
        if request and request.user.is_authenticated and not request.user.is_superuser:
            from organization.models import TenantMembership
            member_tenant_ids = TenantMembership.objects.filter(
                user=request.user
            ).values_list('tenant_id', flat=True)
            self.fields['tenant'] = forms.ModelChoiceField(
                queryset=Tenant.objects.filter(pk__in=member_tenant_ids),
                required=False,
                label=_("Tenant (Optional)"),
                help_text=_("Scope this campaign to a single tenant. Leave blank for a global MSP-wide audit."),
                widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            )
        else:
            self.fields['tenant'] = forms.ModelChoiceField(
                queryset=Tenant.objects.all(),
                required=False,
                label=_("Tenant (Optional)"),
                help_text=_("Scope this campaign to a single tenant. Leave blank for a global MSP-wide audit."),
                widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            )

        self.fields['location'] = forms.ModelChoiceField(
            queryset=Location.objects.all(),
            required=False,
            label=_("Target Location (Optional)"),
            help_text=_("Expected location to audit. Leave blank to audit globally."),
            widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''})
        )

    def clean(self):
        cleaned = super().clean()
        tenant = cleaned.get('tenant')
        location = cleaned.get('location')
        if tenant and location and location.tenant_id and location.tenant_id != tenant.pk:
            self.add_error(
                'location',
                _("The selected location does not belong to the chosen tenant."),
            )
        return cleaned


class AuditBarcodeScanForm(forms.Form):
    barcode = forms.CharField(
        label="Scan Asset Tag or Serial Number",
        widget=forms.TextInput(attrs={
            'placeholder': 'Scan serial or tag...',
            'autofocus': 'autofocus',
            'class': 'form-control',
            'id': 'barcode-scan-input'
        })
    )
