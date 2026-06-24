from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from core.forms import FilterForm, BulkEditForm, scope_tenant_field
from core.managers import get_current_tenant
from core.auth.guards import validate_permission_grant
from ..models import Tenant, TenantRole, TenantMembership
from ..filters import TenantMembershipFilterSet

User = get_user_model()


class TenantMembershipForm(forms.ModelForm):
    user = forms.ModelChoiceField(
        queryset=User.objects.all(),
        required=True,
        label=_("User"),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.all(),
        required=True,
        label=_("Tenant"),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    roles = forms.ModelMultipleChoiceField(
        queryset=TenantRole.objects.all(),
        required=False,
        label=_("Roles"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'})
    )
    direct_permissions = forms.JSONField(
        required=False,
        label=_("Direct permissions"),
        initial=list,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        help_text=_("JSON list of permission codenames, e.g. [\"assets.view_asset\"]"),
    )

    class Meta:
        model = TenantMembership
        fields = ['user', 'tenant', 'roles', 'direct_permissions']

    def __init__(self, *args, user=None, tenant=None, **kwargs):
        self._requesting_user = user
        self._tenant = tenant
        super().__init__(*args, **kwargs)
        scope_tenant_field(self)
        if tenant is not None:
            self.fields['roles'].queryset = TenantRole.objects.filter(tenant=tenant)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'user', 'tenant', 'roles', 'direct_permissions',
        )
        from .helpers import add_standard_buttons
        add_standard_buttons(self.helper, self.instance, 'users:user_list')

    def clean(self):
        cleaned_data = super().clean()
        tenant = cleaned_data.get('tenant')
        roles = cleaned_data.get('roles') or []
        for role in roles:
            if tenant and role.tenant != tenant:
                raise forms.ValidationError(
                    _("Role '%(role)s' does not belong to the selected tenant.") % {'role': role}
                )
        # Escalation guard (centralised in core.auth.guards): a non-superuser may not
        # grant direct_permissions, nor attach roles carrying permissions, that they do
        # not themselves hold in this tenant. Roles are constrained to `tenant` above, so
        # their permissions are evaluated against the membership's tenant.
        granted = set(cleaned_data.get('direct_permissions') or [])
        for role in roles:
            granted.update(role.permissions or [])
        validate_permission_grant(self._requesting_user, granted, tenant)
        return cleaned_data


class TenantMembershipFilterForm(FilterForm):
    filterset_class = TenantMembershipFilterSet


class TenantMembershipBulkRoleForm(BulkEditForm):
    roles_to_add = forms.ModelMultipleChoiceField(
        queryset=TenantRole._base_manager.all(),
        required=False,
        label=_("Add roles"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'})
    )
    roles_to_remove = forms.ModelMultipleChoiceField(
        queryset=TenantRole._base_manager.all(),
        required=False,
        label=_("Remove roles"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop('add_tags', None)
        self.fields.pop('remove_tags', None)


class TenantRoleAssignUsersForm(forms.Form):
    users = forms.ModelMultipleChoiceField(
        queryset=User.objects.all(),
        required=True,
        label=_("Users"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'})
    )
