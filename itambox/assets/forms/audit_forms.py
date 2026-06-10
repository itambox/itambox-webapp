from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Field
from organization.models import Location
from assets.models import StatusLabel


class AssetAuditConfirmForm(forms.Form):
    """Modal form for standalone asset verification (detail-page 'Verify Physical Presence')."""
    location = forms.ModelChoiceField(
        queryset=Location.objects.all().order_by('name'),
        required=True,
        label="Observed Location",
        help_text="Where did you physically find this asset right now?",
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
    )
    status = forms.ModelChoiceField(
        queryset=StatusLabel.objects.exclude(type=StatusLabel.TYPE_ARCHIVED).order_by('name'),
        required=True,
        label="Observed Status",
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
    )
    notes = forms.CharField(
        required=False,
        label="Notes (optional)",
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
    )

    def __init__(self, *args, asset=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Re-evaluate querysets at instantiation time so TenantScopingManager
        # picks up the active tenant context from the current request.
        self.fields['location'].queryset = Location.objects.all().order_by('name')
        self.fields['status'].queryset = StatusLabel.objects.exclude(
            type=StatusLabel.TYPE_ARCHIVED
        ).order_by('name')
        if asset:
            self.fields['location'].initial = asset.location_id
            self.fields['status'].initial = asset.status_id
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Field('location'),
            Field('status'),
            Field('notes'),
        )


# Legacy ModelForm kept for API/compliance barcode views — not used by the detail modal.
class AuditSessionForm(forms.Form):
    pass  # defined in compliance.forms_audit; imported from there
