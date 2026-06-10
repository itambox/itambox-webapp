from django import forms
from assets.models import StatusLabel
from compliance.models import AssetAudit, AuditSession
from organization.models import Location


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
    class Meta:
        model = AuditSession
        fields = ['name', 'location']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['location'] = forms.ModelChoiceField(
            queryset=Location.objects.all(),
            required=False,
            label="Target Location (Optional)",
            help_text="Expected location to audit. Leave blank to audit globally.",
            widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''})
        )


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
