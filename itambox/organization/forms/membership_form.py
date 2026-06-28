"""Unified Membership form (tenant member, contact, provider staff)."""
from django import forms
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout

from core.forms import FilterForm, BulkEditForm
from core.auth.guards import validate_permission_grant
from ..models import Membership, Role, Tenant, Provider, TenantGroup

User = get_user_model()


class MembershipForm(forms.ModelForm):
    """Single ModelForm for every Membership kind.

    The form decides container + person_type from caller-supplied kwargs or the bound
    instance, then exposes the right fields for the case (tenant_scope/assigned_tenants
    only on staff memberships, etc.).
    """
    user = forms.ModelChoiceField(
        queryset=User.objects.all(),
        required=True, label=_("User"),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    person_type = forms.ChoiceField(
        choices=Membership.PERSON_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Person type"),
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
    direct_permissions = forms.JSONField(
        required=False, initial=list, label=_("Direct permissions"),
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        help_text=_('JSON list of permission codenames, e.g. ["assets.view_asset"]'),
    )
    tenant_scope = forms.ChoiceField(
        choices=[('', _('—'))] + Membership.SCOPE_CHOICES, required=False,
        label=_("Tenant scope"),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    scope_group = forms.ModelChoiceField(
        queryset=TenantGroup._base_manager.none(), required=False, label=_("Scope group"),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    assigned_tenants = forms.ModelMultipleChoiceField(
        queryset=Tenant._base_manager.none(), required=False, label=_("Assigned tenants"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = Membership
        fields = [
            'user', 'person_type', 'tenant', 'provider',
            'roles', 'direct_permissions',
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

        # ``person_type`` is required=False so callers can submit short forms; when not
        # supplied it falls back to initial (set below) or defaults to ``member`` in clean().
        self.fields['person_type'].required = False

        # Default person_type from context
        if not self.instance.pk:
            if self._provider_ctx is not None:
                self.fields['person_type'].initial = Membership.PERSON_STAFF
                self.fields['provider'].initial = self._provider_ctx.pk
                self.fields['provider'].widget = forms.HiddenInput()
            elif self._tenant_ctx is not None:
                self.fields['person_type'].initial = Membership.PERSON_MEMBER
                self.fields['tenant'].initial = self._tenant_ctx.pk
                self.fields['tenant'].widget = forms.HiddenInput()
            else:
                self.fields['person_type'].initial = Membership.PERSON_MEMBER
        else:
            # Edit — container is immutable.
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

        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'user', 'person_type', 'tenant', 'provider',
            'roles', 'direct_permissions',
            'tenant_scope', 'scope_group', 'assigned_tenants', 'is_active',
        )
        from .helpers import add_standard_buttons
        add_standard_buttons(self.helper, self.instance, 'organization:membership_list')

    def clean(self):
        cleaned = super().clean()
        person_type = (
            cleaned.get('person_type')
            or (self.instance.person_type if self.instance.pk else None)
            or self.fields['person_type'].initial
            or Membership.PERSON_MEMBER
        )
        tenant = cleaned.get('tenant') or (self.instance.tenant if self.instance.pk else None)
        provider = cleaned.get('provider') or (self.instance.provider if self.instance.pk else None)
        roles = cleaned.get('roles') or []

        # Container/person_type consistency.
        if person_type == Membership.PERSON_STAFF:
            if provider is None:
                raise forms.ValidationError(_("Provider staff memberships require a Provider."))
            tenant = None
            cleaned['tenant'] = None
            cleaned['provider'] = provider
            self.instance.tenant = None
            self.instance.provider = provider
        else:  # member / contact
            if tenant is None:
                raise forms.ValidationError(_("Tenant memberships require a Tenant."))
            provider = None
            cleaned['provider'] = None
            cleaned['tenant'] = tenant
            self.instance.provider = None
            self.instance.tenant = tenant

        self.instance.person_type = person_type

        # Roles must match the container.
        container = provider if person_type == Membership.PERSON_STAFF else tenant
        expected_scope = Role.SCOPE_PROVIDER if person_type == Membership.PERSON_STAFF else Role.SCOPE_TENANT
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
        if person_type != Membership.PERSON_STAFF:
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
                self.add_error('scope_group', _("A scope group is required when tenant scope is 'Tenant group'."))

        # Escalation guard — union of direct + role permissions, evaluated against the
        # role's own container (tenant for member/contact, provider for staff).
        granted = set(cleaned.get('direct_permissions') or [])
        for role in roles:
            granted.update(role.permissions or [])
        validate_permission_grant(self._requesting_user, granted, container)
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=commit)
        if commit:
            cleaned = self.cleaned_data
            if instance.person_type == Membership.PERSON_STAFF:
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
