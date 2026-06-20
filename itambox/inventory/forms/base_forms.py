from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from organization.models import Location, AssetHolder
from assets.models import Asset


class BaseCheckoutForm(forms.Form):
    assigned_holder = forms.ModelChoiceField(
        queryset=AssetHolder.objects.all().order_by('last_name', 'first_name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Assign to Asset Holder")
    )
    assigned_location = forms.ModelChoiceField(
        queryset=Location.objects.all().select_related('site').order_by('site__name', 'name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Assign to Location")
    )
    assigned_asset = forms.ModelChoiceField(
        queryset=Asset.objects.all().order_by('asset_tag'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Assign to Asset")
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        label=_("Notes")
    )

    def __init__(self, *args, **kwargs):
        tenant = kwargs.pop('tenant', None)
        super().__init__(*args, **kwargs)
        if tenant:
            # Rescope every tenant-owned FK choice to the item's tenant. These
            # querysets are import-frozen and unscoped, so without this a checkout
            # could target (and expose in the dropdown) another tenant's holders,
            # locations or assets. Subclasses contribute their own Location source
            # field (source_location / from_location), scoped here as well.
            self.fields['assigned_holder'].queryset = AssetHolder.objects.filter(
                tenant=tenant).order_by('last_name', 'first_name')
            self.fields['assigned_location'].queryset = Location.objects.filter(
                tenant=tenant).select_related('site').order_by('site__name', 'name')
            self.fields['assigned_asset'].queryset = Asset.objects.filter(
                tenant=tenant).order_by('asset_tag')
            for loc_field in ('source_location', 'from_location'):
                if loc_field in self.fields:
                    self.fields[loc_field].queryset = Location.objects.filter(
                        tenant=tenant).order_by('name')

    def clean(self):
        cleaned_data = super().clean()
        holder = cleaned_data.get('assigned_holder')
        location = cleaned_data.get('assigned_location')
        asset = cleaned_data.get('assigned_asset')

        filled = [t for t in [holder, location, asset] if t is not None]
        if len(filled) == 0:
            raise ValidationError(_("You must select either an Asset Holder, a Location, or an Asset."))
        if len(filled) > 1:
            raise ValidationError(_("Please select exactly one target (either Asset Holder, Location, OR Asset)."))
        return cleaned_data
