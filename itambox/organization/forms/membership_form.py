"""Unified Membership form (tenant member, contact, provider staff)."""
from django import forms
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Fieldset

from core.forms import FilterForm, BulkEditForm
from core.auth.guards import validate_permission_grant
from ..models import Membership, Role, Tenant, Provider, TenantGroup

User = get_user_model()


class MembershipForm(forms.ModelForm):
    """Single ModelForm for every Membership kind.

    The container the form is bound to *is* the kind — pick a tenant for a member, a
    provider for staff. There is no ``person_type`` control: provider-staff scoping fields
    (``tenant_scope`` / ``scope_group`` / ``assigned_tenants``) appear only when the
    container is a provider.
    """
    user = forms.ModelChoiceField(
        queryset=User.objects.all(),
        required=True, label=_("User"),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.none(), required=False, label=_("Tenant"),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    provider = forms.ModelChoiceField(
        queryset=Provider.objects.none(), required=False, label=_("Provider"),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    roles = forms.ModelMultipleChoiceField(
        queryset=Role._base_manager.none(), required=False, label=_("Roles"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )
    tenant_scope = forms.ChoiceField(
        choices=[('', _('—'))] + Membership.SCOPE_CHOICES, required=False,
        label=_("Customer access"),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    scope_group = forms.ModelChoiceField(
        queryset=TenantGroup._base_manager.none(), required=False, label=_("Tenant group"),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    assigned_tenants = forms.ModelMultipleChoiceField(
        queryset=Tenant._base_manager.none(), required=False, label=_("Specific tenants"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = Membership
        fields = [
            'user', 'tenant', 'provider',
            'roles',
            'tenant_scope', 'scope_group', 'assigned_tenants',
            'is_active',
        ]
        widgets = {
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        self._requesting_user = kwargs.pop('user', None)
        self._tenant_ctx = kwargs.pop('tenant', None)
        self._provider_ctx = kwargs.pop('provider', None)
        super().__init__(*args, **kwargs)

        self.fields['user'].queryset = User.objects.order_by('username')

        # Provider-related global pickers must use the unscoped base manager so they're
        # not silently emptied by the active-tenant form-field scoping in core.apps.
        self.fields['tenant'].queryset = Tenant._base_manager.filter(deleted_at__isnull=True).order_by('name')
        self.fields['provider'].queryset = Provider._base_manager.filter(deleted_at__isnull=True).order_by('name')
        self.fields['scope_group'].queryset = TenantGroup._base_manager.filter(deleted_at__isnull=True).order_by('name')
        self.fields['assigned_tenants'].queryset = Tenant._base_manager.filter(deleted_at__isnull=True).order_by('name')

        # The bound container decides the kind — there is no person_type control. Resolve
        # whether this membership is (or will be) provider staff, and lock the container on
        # edit. ``container_known`` is False only on a context-free create where the user
        # still has to pick tenant vs provider.
        container_known = True
        if self.instance.pk:
            is_staff = self.instance.provider_id is not None
            if self.instance.tenant_id:
                self.fields['tenant'].queryset = Tenant._base_manager.filter(pk=self.instance.tenant_id)
                self.fields['tenant'].initial = self.instance.tenant_id
                self.fields['tenant'].disabled = True
                self.fields['provider'].widget = forms.HiddenInput()
            if self.instance.provider_id:
                self.fields['provider'].queryset = Provider._base_manager.filter(pk=self.instance.provider_id)
                self.fields['provider'].initial = self.instance.provider_id
                self.fields['provider'].disabled = True
                self.fields['tenant'].widget = forms.HiddenInput()
            self.fields['user'].disabled = True
        elif self._provider_ctx is not None:
            is_staff = True
            self.fields['provider'].initial = self._provider_ctx.pk
            self.fields['provider'].widget = forms.HiddenInput()
            self.fields['tenant'].widget = forms.HiddenInput()
        elif self._tenant_ctx is not None:
            is_staff = False
            self.fields['tenant'].initial = self._tenant_ctx.pk
            self.fields['tenant'].widget = forms.HiddenInput()
            self.fields['provider'].widget = forms.HiddenInput()
        else:
            # Context-free create: the user picks a tenant or a provider. The kind isn't known
            # yet, so the staff scoping fields stay visible (cleared if a tenant is chosen).
            container_known = False
            is_staff = bool(self.data.get('provider')) if self.is_bound else False

        # Role queryset: tenant-scoped roles for tenant memberships, provider-scoped roles
        # for provider memberships. Falls back to all roles if context is undetermined.
        if self.instance.pk and self.instance.tenant_id:
            self.fields['roles'].queryset = Role._base_manager.filter(
                scope=Role.SCOPE_TENANT, tenant_id=self.instance.tenant_id, deleted_at__isnull=True,
            ).order_by('name')
        elif self.instance.pk and self.instance.provider_id:
            self.fields['roles'].queryset = Role._base_manager.filter(
                scope=Role.SCOPE_PROVIDER, provider_id=self.instance.provider_id, deleted_at__isnull=True,
            ).order_by('name')
        elif self._provider_ctx is not None:
            self.fields['roles'].queryset = Role._base_manager.filter(
                scope=Role.SCOPE_PROVIDER, provider=self._provider_ctx, deleted_at__isnull=True,
            ).order_by('name')
        elif self._tenant_ctx is not None:
            self.fields['roles'].queryset = Role._base_manager.filter(
                scope=Role.SCOPE_TENANT, tenant=self._tenant_ctx, deleted_at__isnull=True,
            ).order_by('name')
        else:
            self.fields['roles'].queryset = Role._base_manager.filter(deleted_at__isnull=True).order_by('scope', 'name')

        # Business-language labels for the provider-staff scoping fields ("Customer access",
        # not "Tenant scope" — which reads as "which tenant the person is in").
        self.fields['tenant_scope'].label = _("Customer access")
        self.fields['tenant_scope'].help_text = _(
            "Which of the provider's customer tenants this technician can reach."
        )
        self.fields['tenant_scope'].choices = [('', _('—'))] + [
            (Membership.SCOPE_EXPLICIT, _('Specific tenants')),
            (Membership.SCOPE_TENANT_GROUP, _('A tenant group + its descendants')),
            (Membership.SCOPE_ALL, _("All of the provider's tenants")),
        ]
        self.fields['scope_group'].label = _("Tenant group")
        self.fields['assigned_tenants'].label = _("Specific tenants")

        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        base = ['user', 'tenant', 'provider', 'roles']
        if container_known and not is_staff:
            # Tenant member: the provider-staff scoping fields are no-ops here — drop them so
            # the form never shows a control that does nothing.
            for fname in ('tenant_scope', 'scope_group', 'assigned_tenants'):
                self.fields.pop(fname, None)
            self.helper.layout = Layout(*base, 'is_active')
        else:
            self.helper.layout = Layout(
                *base,
                Fieldset(
                    str(_("Provider staff — customer access")),
                    'tenant_scope', 'scope_group', 'assigned_tenants',
                ),
                'is_active',
            )
        from .helpers import add_standard_buttons
        add_standard_buttons(self.helper, self.instance, 'organization:membership_list')

    def clean(self):
        cleaned = super().clean()
        tenant = cleaned.get('tenant') or (self.instance.tenant if self.instance.pk else None)
        provider = cleaned.get('provider') or (self.instance.provider if self.instance.pk else None)
        roles = cleaned.get('roles') or []

        # Exactly one container; the populated FK *is* the kind (provider ⇒ staff,
        # tenant ⇒ member).
        if provider is not None and tenant is not None:
            raise forms.ValidationError(_("A membership belongs to either a tenant or a provider, not both."))
        if provider is not None:
            is_staff = True
            tenant = None
            cleaned['tenant'] = None
            cleaned['provider'] = provider
            self.instance.tenant = None
            self.instance.provider = provider
        elif tenant is not None:
            is_staff = False
            provider = None
            cleaned['provider'] = None
            cleaned['tenant'] = tenant
            self.instance.provider = None
            self.instance.tenant = tenant
        else:
            raise forms.ValidationError(_("Pick a tenant (for a member) or a provider (for staff)."))

        # Roles must match the container.
        container = provider if is_staff else tenant
        expected_scope = Role.SCOPE_PROVIDER if is_staff else Role.SCOPE_TENANT
        for role in roles:
            if role.scope != expected_scope:
                raise forms.ValidationError(
                    _("Role '%(role)s' has the wrong scope for this membership.") % {'role': role}
                )
            if role.scope == Role.SCOPE_TENANT and role.tenant_id != getattr(tenant, 'pk', None):
                raise forms.ValidationError(
                    _("Role '%(role)s' does not belong to the selected tenant.") % {'role': role}
                )
            if role.scope == Role.SCOPE_PROVIDER and role.provider_id != getattr(provider, 'pk', None):
                raise forms.ValidationError(
                    _("Role '%(role)s' does not belong to the selected provider.") % {'role': role}
                )

        # Provider-only fields cleared for tenant memberships.
        if not is_staff:
            cleaned['tenant_scope'] = None
            cleaned['scope_group'] = None
            cleaned['assigned_tenants'] = []
            self.instance.tenant_scope = None
            self.instance.scope_group = None
        else:
            scope = cleaned.get('tenant_scope') or Membership.SCOPE_EXPLICIT
            cleaned['tenant_scope'] = scope
            self.instance.tenant_scope = scope
            if scope == Membership.SCOPE_TENANT_GROUP and not cleaned.get('scope_group'):
                self.add_error('scope_group', _("A scope group is required when access is 'A tenant group'."))

        # Escalation guard — union of the selected roles' permissions, evaluated against
        # the role's own container (tenant for member/contact, provider for staff). The
        # form no longer authors ``direct_permissions``; the instance keeps whatever it
        # already had (one-off grants go through roles), so only role perms are guarded.
        granted = set()
        for role in roles:
            granted.update(role.permissions or [])
        validate_permission_grant(self._requesting_user, granted, container)
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=commit)
        if commit:
            cleaned = self.cleaned_data
            if instance.provider_id:  # provider staff
                tenants = cleaned.get('assigned_tenants') or []
                instance.assigned_tenants.set(tenants)
            else:
                instance.assigned_tenants.clear()
        return instance


class MembershipFilterForm(FilterForm):
    from ..filters import MembershipFilterSet
    filterset_class = MembershipFilterSet


class MembershipBulkRoleForm(BulkEditForm):
    """Bulk add/remove roles for selected memberships (all must share a container)."""
    roles_to_add = forms.ModelMultipleChoiceField(
        queryset=Role._base_manager.all(), required=False, label=_("Add roles"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )
    roles_to_remove = forms.ModelMultipleChoiceField(
        queryset=Role._base_manager.all(), required=False, label=_("Remove roles"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop('add_tags', None)
        self.fields.pop('remove_tags', None)
