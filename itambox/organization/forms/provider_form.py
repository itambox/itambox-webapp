"""Provider form + quick-onboard helpers.

Provider-role / ProviderRoleTemplate / ProviderMembership are gone — replaced by the
unified ``Role`` + ``Membership`` models (see ``role_form.py`` / ``membership_form.py``).
This module keeps the basic Provider CRUD form and adds the single-form onboarding flow
the unified RBAC enables:

  * ``TechnicianQuickForm`` — one form to onboard a provider technician.
"""
import secrets

from django import forms
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout

from core.forms import FilterForm

from .helpers import add_standard_buttons
from ..models import Provider, Tenant, TenantGroup, Role, Membership, AssetHolder
from ..filters import ProviderFilterSet

User = get_user_model()


class ProviderForm(forms.ModelForm):
    internal_tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = Provider
        fields = ['name', 'slug', 'description', 'comments', 'internal_tenant']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'comments': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }
        help_texts = {'slug': _('URL-friendly identifier.')}

    def __init__(self, *args, **kwargs):
        kwargs.pop('user', None)
        kwargs.pop('tenant', None)
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'name', 'slug', 'description', 'comments', 'internal_tenant',
        )
        add_standard_buttons(self.helper, self.instance, 'organization:provider_list')


class ProviderFilterForm(FilterForm):
    filterset_class = ProviderFilterSet


# ---------------------------------------------------------------------------
# Quick-onboard forms
# ---------------------------------------------------------------------------

class TechnicianQuickForm(forms.Form):
    """One-step provider technician onboarding.

    Picks (or creates) a User by email, assigns a Role, and binds a provider-staff
    Membership with the chosen customer access — all in one POST. The created/updated User
    is returned so the caller can issue an invitation/password-reset link.
    """
    email = forms.EmailField(
        label=_("Email"), required=True,
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'tech@msp.example'}),
    )
    first_name = forms.CharField(
        label=_("First name"), max_length=100, required=True,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    last_name = forms.CharField(
        label=_("Last name"), max_length=100, required=True,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    provider = forms.ModelChoiceField(
        label=_("Provider"), queryset=Provider._base_manager.filter(deleted_at__isnull=True),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    role = forms.ModelChoiceField(
        label=_("Role"), queryset=Role._base_manager.filter(scope=Role.SCOPE_PROVIDER, deleted_at__isnull=True),
        required=False,
        empty_label=_("No role yet — assign later"),
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text=_("What this technician can do across the provider's customer tenants. "
                    "You can assign or change this later."),
    )
    tenant_scope = forms.ChoiceField(
        label=_("Customer access"),
        choices=[
            (Membership.SCOPE_EXPLICIT, _('Specific tenants')),
            (Membership.SCOPE_TENANT_GROUP, _('A tenant group + its descendants')),
            (Membership.SCOPE_ALL, _("All of the provider's tenants")),
        ],
        initial=Membership.SCOPE_EXPLICIT,
        help_text=_("Which of the provider's customer tenants this technician can reach."),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    scope_group = forms.ModelChoiceField(
        label=_("Tenant group"), queryset=TenantGroup._base_manager.filter(deleted_at__isnull=True),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    assigned_tenants = forms.ModelMultipleChoiceField(
        label=_("Specific tenants"), queryset=Tenant._base_manager.filter(deleted_at__isnull=True),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )

    def __init__(self, *args, **kwargs):
        self._requesting_user = kwargs.pop('user', None)
        kwargs.pop('tenant', None)
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'email', 'first_name', 'last_name',
            'provider', 'role',
            'tenant_scope', 'scope_group', 'assigned_tenants',
        )
        add_standard_buttons(self.helper, instance=None, list_url_name='organization:membership_list')

        # Scope querysets based on the requesting user. Use _base_manager throughout: this is a
        # provider-only onboarding flow that runs with NO active tenant, so the tenant-scoping
        # default manager (Role.objects / Tenant.objects) fails closed to .none() for a
        # non-superuser, silently emptying the role/tenant pickers and rejecting a valid
        # submission as "not one of the available choices". _base_manager is tenant-context-
        # independent; deleted_at is filtered explicitly to preserve soft-delete semantics.
        if self._requesting_user and not self._requesting_user.is_superuser:
            visible_pks = []
            for p in Provider._base_manager.filter(deleted_at__isnull=True):
                if self._requesting_user.has_perm('organization.manage_staff', obj=p):
                    visible_pks.append(p.pk)
            self.fields['provider'].queryset = Provider._base_manager.filter(pk__in=visible_pks, deleted_at__isnull=True)
            self.fields['role'].queryset = Role._base_manager.filter(scope=Role.SCOPE_PROVIDER, provider_id__in=visible_pks, deleted_at__isnull=True)
            self.fields['assigned_tenants'].queryset = Tenant._base_manager.filter(provider_id__in=visible_pks, deleted_at__isnull=True)

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get('role')
        provider = cleaned.get('provider')
        if role and provider and role.provider_id != provider.pk:
            raise forms.ValidationError(_("Selected role does not belong to the selected provider."))
        scope = cleaned.get('tenant_scope')
        if scope == Membership.SCOPE_TENANT_GROUP and not cleaned.get('scope_group'):
            self.add_error('scope_group', _("Pick a tenant group when access is 'A tenant group + its descendants'."))
        if scope == Membership.SCOPE_EXPLICIT and not cleaned.get('assigned_tenants'):
            self.add_error('assigned_tenants', _("Pick at least one tenant for 'Specific tenants'."))

        # Verify requesting user has manage_staff permission on the provider
        if self._requesting_user and provider:
            if not self._requesting_user.is_superuser and not self._requesting_user.has_perm('organization.manage_staff', obj=provider):
                raise forms.ValidationError(_("You do not have permission to manage staff for this provider."))

        # Call the privilege-escalation guard
        if self._requesting_user and role and provider:
            from core.auth.guards import validate_permission_grant
            try:
                validate_permission_grant(self._requesting_user, role.permissions or [], provider)
            except forms.ValidationError as e:
                self.add_error('role', e)

        return cleaned

    def save(self):
        from django.db import transaction
        email = self.cleaned_data['email'].strip().lower()
        with transaction.atomic():
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    'username': email,
                    'first_name': self.cleaned_data['first_name'],
                    'last_name': self.cleaned_data['last_name'],
                    'is_active': True,
                },
            )
            if created:
                user.set_unusable_password()
                user.save(update_fields=['password'])
            membership, _was_created = Membership.objects.get_or_create(
                user=user, provider=self.cleaned_data['provider'],
                defaults={
                    'tenant_scope': self.cleaned_data['tenant_scope'],
                    'scope_group': self.cleaned_data.get('scope_group'),
                    'is_active': True,
                },
            )
            if not _was_created:
                membership.is_active = True
                membership.tenant_scope = self.cleaned_data['tenant_scope']
                membership.scope_group = self.cleaned_data.get('scope_group')
                membership.save()
            role = self.cleaned_data.get('role')
            if role is not None:
                membership.roles.add(role)
            if self.cleaned_data['tenant_scope'] == Membership.SCOPE_EXPLICIT:
                membership.assigned_tenants.set(self.cleaned_data['assigned_tenants'])
            else:
                membership.assigned_tenants.clear()
        return user, membership



