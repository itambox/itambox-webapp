from django import forms
from core.forms import BulkEditForm, scope_tenant_field
from ..models import StatusLabel, AssetRole
from organization.models import Location, Tenant

class AssetBulkEditForm(BulkEditForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        scope_tenant_field(self, autoset_when_single=False)

    status = forms.ModelChoiceField(
        queryset=StatusLabel.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''})
    )
    asset_role = forms.ModelChoiceField(
        queryset=AssetRole.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''})
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''})
    )
    tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''})
    )

    class Meta:
        nullable_fields = ['asset_role', 'location', 'tenant']
