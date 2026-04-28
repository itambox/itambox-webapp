from django import forms
from django.core.exceptions import ValidationError

from assets.models import Asset, AssetType, AssetRequest


class AssetRequestForm(forms.ModelForm):
    class Meta:
        model = AssetRequest
        fields = ['asset_type', 'asset', 'notes']
        widgets = {
            'asset_type': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'asset': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only allow requestable objects
        self.fields['asset_type'].queryset = AssetType.objects.filter(requestable=True)
        # Only allow requestable and deployable assets
        self.fields['asset'].queryset = Asset.objects.filter(
            requestable=True,
            status__type='deployable'
        )

    def clean(self):
        cleaned_data = super().clean()
        asset_type = cleaned_data.get('asset_type')
        asset = cleaned_data.get('asset')

        if not asset_type and not asset:
            raise ValidationError("Either Asset Type or specific Asset must be selected.")
        if asset and asset_type and asset.asset_type != asset_type:
            raise ValidationError({"asset": "Selected asset does not match the chosen asset type."})
        return cleaned_data


class AssetRequestActionForm(forms.Form):
    allocated_asset = forms.ModelChoiceField(
        queryset=Asset.objects.none(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
        label="Allocate Specific Asset"
    )
    response_notes = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
        required=False,
        label="Response/Decision Notes"
    )

    def __init__(self, *args, **kwargs):
        request_instance = kwargs.pop('request_instance', None)
        super().__init__(*args, **kwargs)
        if request_instance:
            target_type = request_instance.asset_type
            if target_type:
                self.fields['allocated_asset'].queryset = Asset.objects.filter(
                    asset_type=target_type,
                    requestable=True,
                    status__type='deployable'
                )
            else:
                self.fields['allocated_asset'].queryset = Asset.objects.filter(
                    requestable=True,
                    status__type='deployable'
                )
