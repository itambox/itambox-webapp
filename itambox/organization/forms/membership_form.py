"""Membership form — thin (user, tenant, is_active) anchor + minimal grant block.

Stage-2 minimal adaptation: the form still authors grants inline, but grants are
per-role ``RoleAssignment`` rows now. Every role selected here shares ONE reach
(and, for managed reach, one refinement) — the stage-3 grants UX will replace
this with true per-assignment authoring. On edit the form loads/reconciles the
assignments at the selected reach only and leaves the other reach's rows alone.
"""
from django import forms
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Fieldset

from core.forms import FilterForm, BulkEditForm
from core.auth.guards import validate_assignment_grant
from organization.access import get_descendant_tenant_group_ids
from .helpers import add_standard_buttons
from ..models import Membership, Role, RoleAssignment, Tenant, TenantGroup

User = get_user_model()


class MembershipForm(forms.ModelForm):
    """ModelForm for ``organization.Membership`` plus a minimal grant block.

    The grant block (roles / reach / refinement) writes ``RoleAssignment`` rows in
    ``save()``; the reach radio's "Managed tenants" choice and the refinement
    fields only render when the membership's tenant manages others.
    """
    user = forms.ModelChoiceField(
        queryset=User.objects.all(),
        required=True, label=_("User"),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    roles = forms.ModelMultipleChoiceField(
        queryset=Role._base_manager.none(), required=False, label=_("Roles"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )
    reach = forms.ChoiceField(
        choices=RoleAssignment.REACH_CHOICES,
        initial=RoleAssignment.REACH_OWN,
        required=False, label=_("Reach"),
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}),
        help_text=_("Where the selected roles apply: inside this tenant, or across "
                    "the tenants it manages."),
    )
    managed_scope = forms.ChoiceField(
        choices=RoleAssignment.SCOPE_CHOICES,
        initial=RoleAssignment.SCOPE_EXPLICIT,
        required=False, label=_("Managed scope"),
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text=_("Which managed tenants the roles reach."),
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
        fields = ['user', 'tenant', 'is_active']
        widgets = {
            'tenant': forms.Select(attrs={'class': 'form-select'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        self._requesting_user = kwargs.pop('user', None)
        self._tenant_ctx = kwargs.pop('tenant', None)
        super().__init__(*args, **kwargs)

        self.fields['user'].queryset = User.objects.order_by('username')

        # Cross-tenant pickers must use the unscoped base manager so they're not
        # silently emptied by the active-tenant form-field scoping in core.apps.
        self.fields['tenant'].queryset = Tenant._base_manager.filter(
            deleted_at__isnull=True).order_by('name')
        self.fields['scope_group'].queryset = TenantGroup._base_manager.filter(
            deleted_at__isnull=True).order_by('name')
        self.fields['assigned_tenants'].queryset = Tenant._base_manager.filter(
            deleted_at__isnull=True).order_by('name')

        # Resolve the membership's tenant: locked on edit, prefilled from context on
        # create, otherwise (context-free create) recovered from POST data so the
        # dependent querysets/choices are built against the tenant being submitted.
        membership_tenant = None
        if self.instance.pk:
            membership_tenant = self.instance.tenant
            self.fields['tenant'].queryset = Tenant._base_manager.filter(
                pk=self.instance.tenant_id)
            self.fields['tenant'].initial = self.instance.tenant_id
            self.fields['tenant'].disabled = True
            self.fields['user'].disabled = True
        elif self._tenant_ctx is not None:
            membership_tenant = self._tenant_ctx
            self.fields['tenant'].initial = membership_tenant.pk
            self.fields['tenant'].widget = forms.HiddenInput()
        elif self.is_bound:
            try:
                membership_tenant = Tenant._base_manager.filter(
                    pk=self.data.get('tenant'), deleted_at__isnull=True,
                ).first()
            except (TypeError, ValueError):  # non-numeric tenant id must not 500
                membership_tenant = None
        self._membership_tenant = membership_tenant

        # Role picker: the tenant's own roles plus roles shared down by its managing
        # organization. Unknown tenant (context-free GET) falls back to all roles;
        # clean() re-validates ownership against the tenant actually submitted.
        role_qs = Role._base_manager.filter(deleted_at__isnull=True)
        if membership_tenant is not None:
            ownership = Q(tenant=membership_tenant)
            if membership_tenant.managed_by_id:
                ownership |= Q(
                    tenant_id=membership_tenant.managed_by_id, shared_with_managed=True,
                )
            role_qs = role_qs.filter(ownership)
        self.fields['roles'].queryset = role_qs.order_by('name')

        # Managed reach only exists on managing (is_provider) tenants: elsewhere the
        # radio collapses to its only valid choice and the refinements disappear.
        offer_managed = membership_tenant is None or membership_tenant.is_provider
        if not offer_managed:
            self.fields['reach'].choices = [
                (RoleAssignment.REACH_OWN, dict(RoleAssignment.REACH_CHOICES)[RoleAssignment.REACH_OWN]),
            ]
            self.fields['reach'].widget = forms.HiddenInput()
            for fname in ('managed_scope', 'scope_group', 'assigned_tenants'):
                self.fields.pop(fname, None)
        elif membership_tenant is not None:
            self.fields['assigned_tenants'].queryset = Tenant._base_manager.filter(
                managed_by=membership_tenant, deleted_at__isnull=True,
            ).order_by('name')

        # Edit: seed the grant block from the existing assignments. Own-reach rows win
        # when both reaches exist (managed rows then stay untouched by this form).
        if self.instance.pk:
            own_role_ids = list(
                self.instance.assignments.filter(
                    reach=RoleAssignment.REACH_OWN,
                ).values_list('role_id', flat=True)
            )
            managed = list(
                self.instance.assignments.filter(
                    reach=RoleAssignment.REACH_MANAGED,
                ).select_related('scope_group')
            )
            if own_role_ids or not managed:
                self.fields['roles'].initial = own_role_ids
                self.fields['reach'].initial = RoleAssignment.REACH_OWN
            else:
                self.fields['roles'].initial = [a.role_id for a in managed]
                self.fields['reach'].initial = RoleAssignment.REACH_MANAGED
                first = managed[0]
                if 'managed_scope' in self.fields:
                    self.fields['managed_scope'].initial = (
                        first.managed_scope or RoleAssignment.SCOPE_EXPLICIT
                    )
                    self.fields['scope_group'].initial = first.scope_group_id
                    self.fields['assigned_tenants'].initial = list(
                        first.assigned_tenants.values_list('pk', flat=True)
                    )

        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        if 'managed_scope' in self.fields:
            self.helper.layout = Layout(
                'user', 'tenant', 'roles', 'reach',
                Fieldset(
                    str(_("Managed tenants — coverage")),
                    'managed_scope', 'scope_group', 'assigned_tenants',
                ),
                'is_active',
            )
        else:
            self.helper.layout = Layout('user', 'tenant', 'roles', 'reach', 'is_active')
        add_standard_buttons(self.helper, self.instance, 'organization:membership_list')

    # ------------------------------------------------------------------ cleaning
    def clean(self):
        cleaned = super().clean()
        tenant = cleaned.get('tenant') or (self.instance.tenant if self.instance.pk else None)
        if tenant is None:
            raise forms.ValidationError(_("Pick the tenant this membership belongs to."))
        cleaned['tenant'] = tenant

        roles = list(cleaned.get('roles') or [])
        reach = cleaned.get('reach') or RoleAssignment.REACH_OWN
        cleaned['reach'] = reach

        requested_tenant_ids = None
        if reach == RoleAssignment.REACH_MANAGED:
            if not tenant.is_provider:
                self.add_error('reach', _(
                    "Managed reach requires a tenant that manages others."
                ))
                return cleaned
            scope = cleaned.get('managed_scope') or RoleAssignment.SCOPE_EXPLICIT
            cleaned['managed_scope'] = scope
            scope_group = cleaned.get('scope_group')
            assigned = list(cleaned.get('assigned_tenants') or [])
            if scope == RoleAssignment.SCOPE_TENANT_GROUP:
                if not scope_group:
                    self.add_error('scope_group', _(
                        "A tenant group is required when coverage is 'A tenant group + its descendants'."
                    ))
                    return cleaned
                requested_tenant_ids = set(
                    Tenant._base_manager.filter(
                        managed_by=tenant,
                        group_id__in=get_descendant_tenant_group_ids(scope_group.pk),
                    ).values_list('pk', flat=True)
                )
            elif scope == RoleAssignment.SCOPE_EXPLICIT:
                if not assigned:
                    self.add_error('assigned_tenants', _(
                        "Pick at least one tenant for 'Specific tenants'."
                    ))
                    return cleaned
                outside = [t for t in assigned if t.managed_by_id != tenant.pk]
                if outside:
                    self.add_error('assigned_tenants', _(
                        "These tenants are not managed by %(tenant)s: %(names)s"
                    ) % {'tenant': tenant, 'names': ', '.join(str(t) for t in outside)})
                    return cleaned
                requested_tenant_ids = {t.pk for t in assigned}
            else:  # SCOPE_ALL → requested_tenant_ids stays None (guard semantics)
                requested_tenant_ids = None
        else:
            cleaned['managed_scope'] = None
            cleaned['scope_group'] = None
            cleaned['assigned_tenants'] = []

        # Each role must be assignable inside this tenant: owned by it, or shared
        # down by its managing organization.
        for role in roles:
            if role.tenant_id == tenant.pk:
                continue
            if role.shared_with_managed and tenant.managed_by_id == role.tenant_id:
                continue
            raise forms.ValidationError(
                _("Role '%(role)s' is not available in the selected tenant.") % {'role': role}
            )

        # Escalation guards, one per grant (aggregated so the admin sees all failures).
        errors = []
        for role in roles:
            try:
                validate_assignment_grant(
                    self._requesting_user, role, tenant,
                    reach=reach, requested_tenant_ids=requested_tenant_ids,
                )
            except forms.ValidationError as exc:
                errors.extend(exc.messages)
        if errors:
            raise forms.ValidationError(errors)
        return cleaned

    # ------------------------------------------------------------------ saving
    def save(self, commit=True):
        instance = super().save(commit=commit)
        if commit:
            self._sync_assignments(instance)
        return instance

    def _sync_assignments(self, membership):
        """Reconcile the membership's assignments at the selected reach.

        Roles deselected at that reach lose their assignment; rows at the other
        reach are never touched. Deletes go through per-object ``delete()`` so
        change logging records each revocation.
        """
        cleaned = self.cleaned_data
        roles = list(cleaned.get('roles') or [])
        reach = cleaned.get('reach') or RoleAssignment.REACH_OWN
        managed = reach == RoleAssignment.REACH_MANAGED
        managed_scope = (cleaned.get('managed_scope') or RoleAssignment.SCOPE_EXPLICIT) if managed else None
        scope_group = cleaned.get('scope_group') if managed else None
        assigned_tenants = list(cleaned.get('assigned_tenants') or []) if managed else []

        with transaction.atomic():
            stale = membership.assignments.filter(reach=reach)
            if roles:
                stale = stale.exclude(role__in=roles)
            for assignment in stale:
                assignment.delete()

            for role in roles:
                assignment, created = RoleAssignment.objects.get_or_create(
                    membership=membership, role=role, reach=reach,
                    defaults={
                        'managed_scope': managed_scope,
                        'scope_group': scope_group,
                        'granted_by': self._requesting_user,
                    },
                )
                if managed:
                    if not created and (
                        assignment.managed_scope != managed_scope
                        or assignment.scope_group_id != getattr(scope_group, 'pk', None)
                    ):
                        assignment.managed_scope = managed_scope
                        assignment.scope_group = scope_group
                        assignment.save()
                    assignment.assigned_tenants.set(
                        assigned_tenants if managed_scope == RoleAssignment.SCOPE_EXPLICIT else []
                    )


class MembershipFilterForm(FilterForm):
    from ..filters import MembershipFilterSet  # inline import: breaks forms <-> filters cycle at import time
    filterset_class = MembershipFilterSet


class MembershipBulkRoleForm(BulkEditForm):
    """Bulk add/remove own-reach role assignments for selected memberships.

    The bulk view resolves each membership's tenant, validates role availability
    there, and calls ``validate_assignment_grant`` per (membership, role) before
    creating/deleting ``RoleAssignment`` rows with ``reach='own'``.
    """
    roles_to_add = forms.ModelMultipleChoiceField(
        queryset=Role._base_manager.filter(deleted_at__isnull=True),
        required=False, label=_("Add roles"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )
    roles_to_remove = forms.ModelMultipleChoiceField(
        queryset=Role._base_manager.filter(deleted_at__isnull=True),
        required=False, label=_("Remove roles"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop('add_tags', None)
        self.fields.pop('remove_tags', None)
