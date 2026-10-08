from django import forms

from core.forms import BulkEditForm, TenantScopedFormMixin
from organization.models import Location, Tenant

from ..models import AssetRole, StatusLabel


class AssetBulkEditForm(TenantScopedFormMixin, BulkEditForm):
    # Bulk edit never requires a target tenant and always shows the picker.
    tenant_required = False
    tenant_autoset_when_single = False

    status = forms.ModelChoiceField(
        queryset=StatusLabel.objects.all(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "data-tom-select": ""}),
    )
    asset_role = forms.ModelChoiceField(
        queryset=AssetRole.objects.all(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "data-tom-select": ""}),
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.all(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "data-tom-select": ""}),
    )
    tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.all(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "data-tom-select": ""}),
    )

    class Meta:
        nullable_fields = ["asset_role", "location", "tenant"]
