from django import forms
from django.core.exceptions import ValidationError

from organization.models import Location, AssetHolder
from assets.models import Asset


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
