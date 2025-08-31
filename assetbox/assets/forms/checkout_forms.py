from django import forms
from django.core.exceptions import ValidationError
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, HTML

from organization.models import Location, AssetHolder
from assets.models import Asset


class AssetCheckOutForm(forms.Form):
    TARGET_CHOICES = [
        ('holder', 'Asset Holder'),
        ('location', 'Location'),
        ('asset', 'Asset'),
    ]

    target_type = forms.ChoiceField(
        choices=TARGET_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Assign to"
    )
    asset_holder = forms.ModelChoiceField(
        queryset=AssetHolder.objects.all().order_by('last_name', 'first_name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Asset Holder"
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.all().select_related('site').order_by('site__name', 'name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Location"
    )
    asset_target = forms.ModelChoiceField(
        queryset=Asset.objects.exclude(status__type='undeployable').order_by('name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Asset"
    )
    expected_checkin = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        label="Expected Checkin Date"
    )
    notes = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
        required=False,
        label="Notes"
    )

    def clean(self):
        cleaned_data = super().clean()
        target_type = cleaned_data.get('target_type')
        holder = cleaned_data.get('asset_holder')
        location = cleaned_data.get('location')
        asset_target = cleaned_data.get('asset_target')

        if target_type == 'holder' and not holder:
            raise ValidationError("Must select an Asset Holder.", code='holder_required')
        if target_type == 'location' and not location:
            raise ValidationError("Must select a Location.", code='location_required')
        if target_type == 'asset' and not asset_target:
            raise ValidationError("Must select an Asset.", code='asset_required')
        if not target_type:
            raise ValidationError("Must select a target type.", code='target_type_required')
        return cleaned_data

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            'target_type',
            'asset_holder',
            'location',
            'asset_target',
            'expected_checkin',
            'notes',
        )

from inventory.forms import BaseCheckoutForm, AccessoryCheckoutForm, ConsumableCheckoutForm, KitCheckoutForm
