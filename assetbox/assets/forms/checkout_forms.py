from django import forms
from django.core.exceptions import ValidationError
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, HTML

from organization.models import Location, AssetHolder

class AssetCheckOutForm(forms.Form):
    asset_holder = forms.ModelChoiceField(
        queryset=AssetHolder.objects.all().order_by('last_name', 'first_name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Assign to Asset Holder"
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.all().select_related('site').order_by('site__name', 'name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Assign to Location"
    )

    def clean(self):
        cleaned_data = super().clean()
        asset_holder = cleaned_data.get("asset_holder")
        location = cleaned_data.get("location")

        if not asset_holder and not location:
            raise ValidationError(
                "You must select either an Asset Holder or a Location.",
                code='assignment_or_location_required'
            )
        
        if asset_holder and location:
            raise ValidationError(
                "Please select either an Asset Holder OR a Location, not both.",
                code='multiple_assignments_locations'
            )
        return cleaned_data 

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            'asset_holder',
            HTML('<p class="text-muted text-center my-2">OR</p>'),
            'location',
        )

from inventory.forms import BaseCheckoutForm, AccessoryCheckoutForm, ConsumableCheckoutForm, KitCheckoutForm
