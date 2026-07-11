"""Quick-onboard helpers for managing (MSP) organizations.

The Provider model and its CRUD forms are gone — a managing organization is just a
Tenant with ``is_provider=True``. What remains here is the single-form onboarding
flow: ``TechnicianQuickForm`` onboards a technician as a member of the managing
organization with a managed-reach role assignment, in one POST.

(File/class name kept for stage 2; renaming is a stage-3/4 concern.)
"""
from django import forms
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout

from core.auth.guards import validate_assignment_grant
from organization.access import get_descendant_tenant_group_ids

from .helpers import add_standard_buttons
from ..models import Membership, Role, RoleAssignment, Tenant, TenantGroup

User = get_user_model()


def _managing_tenants_for(user):
    """The ``is_provider`` tenants where ``user`` may onboard staff.

    Superusers (and an absent user) see every managing tenant; everyone else only
    those where they hold ``organization.add_membership``. Uses ``_base_manager``:
    this flow runs without an active-tenant context, where the tenant-scoping
    default manager fails closed to ``.none()``.
    """
    qs = Tenant._base_manager.filter(
        is_provider=True, deleted_at__isnull=True,
    ).order_by('name')
    if user is None or getattr(user, 'is_superuser', False):
        return qs
    visible = [
        t.pk for t in qs
        if user.has_perm('organization.add_membership', obj=t)
    ]
    return qs.filter(pk__in=visible)


class TechnicianQuickForm(forms.Form):
    """One-step technician onboarding for a managing organization.

    Picks (or creates) a User by email, binds a Membership at the managing
    organization, and grants the chosen role with managed reach + coverage — all in
    one POST. Returns the (user, membership) pair so the caller can issue a
    password-reset link.
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
    organization = forms.ModelChoiceField(
        label=_("Managing organization"),
        queryset=Tenant._base_manager.none(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text=_("The organization this technician works for."),
    )
    role = forms.ModelChoiceField(
        label=_("Role"), queryset=Role._base_manager.none(),
        required=False,
        empty_label=_("No role yet — assign later"),
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text=_("What this technician can do across the organization's managed tenants. "
                    "You can assign or change this later."),
    )
    managed_scope = forms.ChoiceField(
        label=_("Customer access"),
        choices=RoleAssignment.SCOPE_CHOICES,
        initial=RoleAssignment.SCOPE_EXPLICIT,
        help_text=_("Which of the organization's managed tenants this technician can reach."),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    scope_group = forms.ModelChoiceField(
        label=_("Tenant group"),
        queryset=TenantGroup._base_manager.filter(deleted_at__isnull=True),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    assigned_tenants = forms.ModelMultipleChoiceField(
        label=_("Specific tenants"),
        queryset=Tenant._base_manager.none(),
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
            'organization', 'role',
            'managed_scope', 'scope_group', 'assigned_tenants',
        )
        add_standard_buttons(self.helper, instance=None, list_url_name='organization:membership_list')

        # Scope the pickers to the organizations the actor may onboard staff for.
        # _base_manager throughout — see _managing_tenants_for.
        orgs = _managing_tenants_for(self._requesting_user)
        org_ids = list(orgs.values_list('pk', flat=True))
        self.fields['organization'].queryset = orgs
        self.fields['role'].queryset = Role._base_manager.filter(
            tenant_id__in=org_ids, deleted_at__isnull=True,
        ).order_by('name')
        self.fields['assigned_tenants'].queryset = Tenant._base_manager.filter(
            managed_by_id__in=org_ids, deleted_at__isnull=True,
        ).order_by('name')

    def clean(self):
        cleaned = super().clean()
        organization = cleaned.get('organization')
        role = cleaned.get('role')
        if role and organization and role.tenant_id != organization.pk:
            raise forms.ValidationError(
                _("Selected role does not belong to the selected organization.")
            )

        scope = cleaned.get('managed_scope')
        scope_group = cleaned.get('scope_group')
        assigned = list(cleaned.get('assigned_tenants') or [])
        if scope == RoleAssignment.SCOPE_TENANT_GROUP and not scope_group:
            self.add_error('scope_group', _(
                "Pick a tenant group when access is 'A tenant group + its descendants'."
            ))
        if scope == RoleAssignment.SCOPE_EXPLICIT and not assigned:
            self.add_error('assigned_tenants', _(
                "Pick at least one tenant for 'Specific tenants'."
            ))
        if organization:
            outside = [t for t in assigned if t.managed_by_id != organization.pk]
            if outside:
                self.add_error('assigned_tenants', _(
                    "These tenants are not managed by %(org)s: %(names)s"
                ) % {'org': organization, 'names': ', '.join(str(t) for t in outside)})

        # Defense in depth: the queryset already restricts choices, but re-check the
        # membership-creation gate explicitly.
        if self._requesting_user and organization:
            if not self._requesting_user.is_superuser and not self._requesting_user.has_perm(
                'organization.add_membership', obj=organization,
            ):
                raise forms.ValidationError(
                    _("You do not have permission to onboard staff for this organization.")
                )

        # Escalation guard on the managed-reach grant.
        if role and organization and not self.errors:
            if scope == RoleAssignment.SCOPE_ALL:
                requested_tenant_ids = None
            elif scope == RoleAssignment.SCOPE_TENANT_GROUP:
                requested_tenant_ids = set(
                    Tenant._base_manager.filter(
                        managed_by=organization,
                        group_id__in=get_descendant_tenant_group_ids(scope_group.pk),
                    ).values_list('pk', flat=True)
                )
            else:
                requested_tenant_ids = {t.pk for t in assigned}
            try:
                validate_assignment_grant(
                    self._requesting_user, role, organization,
                    reach=RoleAssignment.REACH_MANAGED,
                    requested_tenant_ids=requested_tenant_ids,
                )
            except forms.ValidationError as e:
                self.add_error('role', e)

        return cleaned

    def save(self):
        organization = self.cleaned_data['organization']
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
            membership, was_created = Membership.objects.get_or_create(
                user=user, tenant=organization,
                defaults={'is_active': True},
            )
            if not was_created and not membership.is_active:
                membership.is_active = True
                membership.save()
            role = self.cleaned_data.get('role')
            if role is not None:
                scope = self.cleaned_data['managed_scope']
                assignment, assignment_created = RoleAssignment.objects.get_or_create(
                    membership=membership, role=role,
                    reach=RoleAssignment.REACH_MANAGED,
                    defaults={
                        'managed_scope': scope,
                        'scope_group': self.cleaned_data.get('scope_group'),
                        'granted_by': self._requesting_user,
                    },
                )
                if not assignment_created:
                    assignment.managed_scope = scope
                    assignment.scope_group = self.cleaned_data.get('scope_group')
                    assignment.save()
                assignment.assigned_tenants.set(
                    self.cleaned_data['assigned_tenants']
                    if scope == RoleAssignment.SCOPE_EXPLICIT else []
                )
        return user, membership
