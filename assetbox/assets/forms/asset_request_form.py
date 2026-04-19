from django import forms

from ..models import AssetRequest


class AssetRequestForm(forms.ModelForm):
    class Meta:
        model = AssetRequest
        fields = ['asset', 'asset_type', 'notes']
        widgets = {
            'asset': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'asset_type': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }


class AssetRequestResponseForm(forms.ModelForm):
    class Meta:
        model = AssetRequest
        fields = ['status', 'response_notes']
        widgets = {
            'status': forms.Select(attrs={'class': 'form-select'}),
            'response_notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }
