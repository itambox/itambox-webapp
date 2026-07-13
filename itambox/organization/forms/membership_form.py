"""Unified Membership and canonical RoleGrant editor.

Own-tenant roles use direct grants with an ``own`` scope. Provider reach uses one
direct grant plus additive RoleGrantScope children. Every elevated direct grant
requires a reason and future expiration.
"""
from django import forms
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Fieldset

from core.forms import FilterForm, BulkEditForm
from core.auth.guards import validate_group_membership_grant, validate_role_grant
from organization.access import get_descendant_tenant_group_ids
from core.mfa import role_is_privileged
from users.services import (
    AmbiguousEmailError, normalize_email, resolve_existing_user, resolve_or_create_user,
)
from ..models import Membership, Role, RoleGrant, RoleGrantScope, Tenant, TenantGroup

User = get_user_model()


class _RoleLabelMixin:
    """Label shared-in role definitions with their provider.

    ``membership_tenant`` is assigned per field instance (fields are deep-copied,
    so this never leaks between forms). Roles owned by the membership's tenant
    render as their bare name; roles shared down by the managing organization
    render as "Name (from <provider>)". With no tenant context (context-free
    create) fall back to ``str(role)``, which carries the owning tenant.
    """
    membership_tenant = None

    def label_from_instance(self, role):
        if self.membership_tenant is None:
            return str(role)
        if role.tenant_id != self.membership_tenant.pk:
            return _("%(role)s (from %(provider)s)") % {
                'role': role.name, 'provider': role.tenant.name,
            }
        return role.name


class _RolePickerField(_RoleLabelMixin, forms.ModelMultipleChoiceField):
    """Multi-select role picker (own-reach roles)."""


class _RoleChoiceField(_RoleLabelMixin, forms.ModelChoiceField):
    """Single-select role picker (one managed-grant formset row)."""


def _role_assignable_in(role, tenant):
    """Whether ``role`` may be assigned inside ``tenant``: owned by it, or shared
    down by its managing organization."""
    if role.tenant_id == tenant.pk:
        return True
    return bool(
        role.shared_with_managed
        and role.tenant.is_provider
        and tenant.managed_by_id == role.tenant_id
    )


def _roles_visible_in_qs(membership_tenant):
    """Queryset of roles assignable in ``membership_tenant`` (own + shared-down).

    Unknown tenant (context-free GET) falls back to all roles; ``clean()`` and the
    per-row escalation guard re-validate ownership against the tenant submitted.
    """
    qs = Role._base_manager.filter(deleted_at__isnull=True).select_related('tenant')
    if membership_tenant is not None:
        ownership = Q(tenant=membership_tenant)
        if membership_tenant.managed_by_id:
            ownership |= Q(
                tenant_id=membership_tenant.managed_by_id,
                tenant__is_provider=True,
                shared_with_managed=True,
            )
        qs = qs.filter(ownership)
    return qs.order_by('name')


# ---------------------------------------------------------------------------
# Managed-reach grant formset — one row per RoleGrant aggregate
# ---------------------------------------------------------------------------
class ManagedRoleGrantForm(forms.Form):
    """One managed-reach grant: a role plus its own coverage refinement.

    Purely a UI row — it does not persist itself; ``MembershipForm.save()``
    reconciles the whole formset against the membership's existing managed rows.
    ``id`` carries the existing ``RoleGrant`` pk (blank for a new row) so the
    reconciler can preserve provenance on surviving rows.
    """

    id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    role = _RoleChoiceField(
        queryset=Role._base_manager.none(), required=False, label=_("Role"),
        widget=forms.Select(attrs={'class': 'form-select managed-role'}),
    )
    managed_scope = forms.ChoiceField(
        choices=(
            ('explicit', _("Specific tenants")),
            (RoleGrantScope.SCOPE_TENANT_GROUP, _("A tenant group + its descendants")),
            (RoleGrantScope.SCOPE_ALL_MANAGED, _("All managed tenants")),
        ),
        initial='explicit',
        required=False, label=_("Coverage"),
        widget=forms.Select(attrs={'class': 'form-select managed-scope'}),
    )
    scope_group = forms.ModelChoiceField(
        queryset=TenantGroup._base_manager.none(), required=False, label=_("Tenant group"),
        widget=forms.Select(attrs={'class': 'form-select managed-scope-group'}),
    )
    assigned_tenants = forms.ModelMultipleChoiceField(
        queryset=Tenant._base_manager.none(), required=False, label=_("Specific tenants"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select managed-assigned-tenants'}),
    )
    reason = forms.CharField(
        required=False, label=_("Reason"), widget=forms.Textarea(attrs={'rows': 2}),
        help_text=_("Required when this is an elevated direct grant."),
    )
    valid_until = forms.DateTimeField(
        required=False, label=_("Valid until"),
        widget=forms.DateTimeInput(
            format='%Y-%m-%dT%H:%M',
            attrs={'type': 'datetime-local'},
        ),
        help_text=_("Required and must be in the future for elevated direct grants."),
    )

    def __init__(self, *args, membership_tenant=None, requesting_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._membership_tenant = membership_tenant
        self._requesting_user = requesting_user
        self.fields['role'].queryset = _roles_visible_in_qs(membership_tenant)
        self.fields['role'].membership_tenant = membership_tenant
        self.fields['scope_group'].queryset = TenantGroup._base_manager.filter(
            deleted_at__isnull=True).order_by('name')
        if membership_tenant is not None:
            self.fields['assigned_tenants'].queryset = Tenant._base_manager.filter(
                managed_by=membership_tenant, deleted_at__isnull=True,
            ).order_by('name')
        else:
            self.fields['assigned_tenants'].queryset = Tenant._base_manager.filter(
                deleted_at__isnull=True).order_by('name')

    def is_blank(self):
        """A row the user never touched (no role selected) is ignored, not errored."""
        return not (self.cleaned_data.get('role') if hasattr(self, 'cleaned_data') else None)

    def clean(self):
        cleaned = super().clean()
        # Deleted or entirely-blank rows carry no grant and are skipped.
        if cleaned.get('DELETE'):
            return cleaned
        role = cleaned.get('role')
        if not role:
            return cleaned

        tenant = self._membership_tenant
        if tenant is None or not tenant.is_provider:
            raise forms.ValidationError(_(
                "Managed grants require a managing (provider) tenant."
            ))

        scope = cleaned.get('managed_scope') or 'explicit'
        cleaned['managed_scope'] = scope
        requested_tenant_ids = None
        if scope == RoleGrantScope.SCOPE_TENANT_GROUP:
            cleaned['assigned_tenants'] = []
            scope_group = cleaned.get('scope_group')
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
        elif scope == 'explicit':
            cleaned['scope_group'] = None
            assigned = list(cleaned.get('assigned_tenants') or [])
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
            cleaned['scope_group'] = None
            cleaned['assigned_tenants'] = []

        # Role must be assignable inside this tenant (owned or shared-down).
        if not _role_assignable_in(role, tenant):
            raise forms.ValidationError(
                _("Role '%(role)s' is not available in the selected tenant.") % {'role': role}
            )

        # Escalation guard for this one managed row — a single invalid row makes
        # the formset (and thus the whole transaction) fail.
        if role_is_privileged(role):
            reason = (cleaned.get('reason') or '').strip()
            valid_until = cleaned.get('valid_until')
            if not reason:
                self.add_error('reason', _("Elevated direct grants require a reason."))
            if valid_until is None:
                self.add_error('valid_until', _("Elevated direct grants require an expiration."))
            elif valid_until <= timezone.now():
                self.add_error('valid_until', _("The expiration must be in the future."))
            cleaned['reason'] = reason

        try:
            validate_role_grant(
                self._requesting_user, role, tenant,
                scope_type=scope,
                requested_tenant_ids=requested_tenant_ids,
            )
        except forms.ValidationError as exc:
            raise forms.ValidationError(exc.messages)
        return cleaned


class BaseManagedRoleGrantFormSet(forms.BaseFormSet):
    """Rejects two managed rows for the same role (they'd collide on the unique
    ``(membership, role, reach)`` grant constraint)."""

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        seen = set()
        for form in self.forms:
            if not hasattr(form, 'cleaned_data'):
                continue
            cd = form.cleaned_data
            if cd.get('DELETE') or not cd.get('role'):
                continue
            role = cd['role']
            if role.pk in seen:
                raise forms.ValidationError(_(
                    "Role '%(role)s' is granted twice in Managed tenants — combine the "
                    "coverage into one row."
                ) % {'role': role})
            seen.add(role.pk)


ManagedRoleGrantFormSet = forms.formset_factory(
    ManagedRoleGrantForm,
    formset=BaseManagedRoleGrantFormSet,
    extra=1, can_delete=True,
)

MANAGED_FORMSET_PREFIX = 'managed'


class MembershipForm(forms.ModelForm):
    """ModelForm for ``organization.Membership`` — the unified, lossless grant flow.

    Who / This-organization / Managed-tenants sections (see module docstring). The
    Who block only exists on create (user is immutable on edit); the Managed block
    only renders when the membership's tenant is a managing (``is_provider``)
    tenant — or is not yet known (context-free create), in which case ``clean()``
    and the formset re-validate against the tenant actually posted.
    """

    WHO_EXISTING = 'existing'
    WHO_NEW = 'new'
    WHO_CHOICES = [
        (WHO_EXISTING, _("Existing user")),
        (WHO_NEW, _("New user")),
    ]

    PRESET_TECHNICIAN = 'technician'

    who = forms.ChoiceField(
        choices=WHO_CHOICES, initial=WHO_EXISTING, required=False, label=_("Who"),
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}),
    )
    user = forms.ModelChoiceField(
        queryset=User.objects.all(),
        required=False, label=_("User"),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    new_user_email = forms.EmailField(
        required=False, label=_("Email"),
        widget=forms.EmailInput(attrs={
            'class': 'form-control', 'placeholder': 'person@example.com',
        }),
        help_text=_("An existing account with this email is reused; otherwise a new "
                    "user without a password is created — send them a password setup "
                    "link afterwards."),
    )
    new_user_first_name = forms.CharField(
        max_length=150, required=False, label=_("First name"),
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    new_user_last_name = forms.CharField(
        max_length=150, required=False, label=_("Last name"),
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    own_roles = _RolePickerField(
        queryset=Role._base_manager.none(), required=False, label=_("Roles"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
        help_text=_("Roles that apply inside this organization. This tenant's roles, "
                    "plus definitions shared down by its managing organization."),
    )
    reason = forms.CharField(
        required=False,
        label=_("Reason for new elevated direct grants"),
        widget=forms.Textarea(attrs={'rows': 2}),
        help_text=_("Required when adding an elevated role directly to this membership."),
    )
    valid_until = forms.DateTimeField(
        required=False,
        label=_("Expiry for new elevated direct grants"),
        widget=forms.DateTimeInput(
            format='%Y-%m-%dT%H:%M',
            attrs={'type': 'datetime-local'},
        ),
        help_text=_("Required and must be in the future for new elevated direct grants."),
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
        self._preset = kwargs.pop('preset', None)
        super().__init__(*args, **kwargs)
        # Keep the persisted state before ModelForm applies submitted values to
        # ``instance``. Reactivation restores both direct grants and every group
        # grant inherited through this Membership.
        self._initial_is_active = (
            self.instance.is_active if self.instance.pk else None
        )

        #: Set by save() when the who-block created a brand-new user, so the view
        #: can surface the "send password setup link" hint.
        self.new_user_created = False
        self._existing_user_by_email = None

        self.fields['user'].queryset = User.objects.order_by('username')

        # Cross-tenant pickers must use the unscoped base manager so they're not
        # silently emptied by the active-tenant form-field scoping in core.apps.
        self.fields['tenant'].queryset = Tenant._base_manager.filter(
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
            self.fields['tenant'].queryset = Tenant._base_manager.filter(
                pk=self._tenant_ctx.pk)
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

        # Who block only exists on create; on edit the user is immutable.
        if self.instance.pk:
            for fname in ('who', 'new_user_email', 'new_user_first_name',
                          'new_user_last_name'):
                self.fields.pop(fname, None)

        # own_roles: the tenant's own roles plus roles shared down by its managing
        # organization. Unknown tenant (context-free GET) falls back to all roles.
        self.fields['own_roles'].queryset = _roles_visible_in_qs(membership_tenant)
        self.fields['own_roles'].membership_tenant = membership_tenant

        # The Managed block (per-grant formset) only exists on managing
        # (is_provider) tenants: elsewhere every grant is own reach implicitly.
        offer_managed = membership_tenant is None or membership_tenant.is_provider

        # Seed own_roles + the managed formset losslessly from the existing rows.
        managed_initial = []
        self._existing_own_role_ids = set()
        if self.instance.pk:
            self._existing_own_role_ids = set(
                self.instance.role_grants.filter(
                    scopes__scope_type=RoleGrantScope.SCOPE_OWN,
                    role__deleted_at__isnull=True,
                ).filter(
                    Q(valid_until__isnull=True) | Q(valid_until__gt=timezone.now())
                ).values_list('role_id', flat=True).distinct()
            )
            self.fields['own_roles'].initial = sorted(self._existing_own_role_ids)
            grants = self.instance.role_grants.filter(
                role__deleted_at__isnull=True,
                scopes__scope_type__in=(
                    RoleGrantScope.SCOPE_TENANT,
                    RoleGrantScope.SCOPE_TENANT_GROUP,
                    RoleGrantScope.SCOPE_ALL_MANAGED,
                ),
            ).filter(
                Q(valid_until__isnull=True) | Q(valid_until__gt=timezone.now())
            ).select_related('role').prefetch_related(
                'scopes', 'scopes__tenant', 'scopes__tenant_group',
            ).distinct()
            for grant in grants:
                scopes = list(grant.scopes.all())
                if any(s.scope_type == RoleGrantScope.SCOPE_ALL_MANAGED for s in scopes):
                    scope = RoleGrantScope.SCOPE_ALL_MANAGED
                    scope_group_id = None
                    tenant_ids = []
                else:
                    group_scope = next(
                        (s for s in scopes if s.scope_type == RoleGrantScope.SCOPE_TENANT_GROUP),
                        None,
                    )
                    if group_scope is not None:
                        scope = RoleGrantScope.SCOPE_TENANT_GROUP
                        scope_group_id = group_scope.tenant_group_id
                        tenant_ids = []
                    else:
                        scope = 'explicit'
                        scope_group_id = None
                        tenant_ids = [
                            s.tenant_id for s in scopes
                            if s.scope_type == RoleGrantScope.SCOPE_TENANT and s.tenant_id
                        ]
                managed_initial.append({
                    'id': grant.pk,
                    'role': grant.role_id,
                    'managed_scope': scope,
                    'scope_group': scope_group_id,
                    'assigned_tenants': tenant_ids,
                    'reason': grant.reason,
                    'valid_until': grant.valid_until,
                })
        elif not self.is_bound and self._preset == self.PRESET_TECHNICIAN \
                and membership_tenant is not None and membership_tenant.is_provider:
            managed_initial = self._technician_preset_rows(membership_tenant)

        self.managed_formset = self._build_managed_formset(
            offer_managed, membership_tenant, managed_initial,
        )

        self.helper = FormHelper(self)
        self.helper.form_tag = False  # the template wraps the <form> so it can embed the formset
        self.helper.disable_csrf = True
        self.helper.layout = Layout(*self._layout_items())

    def _build_managed_formset(self, offer_managed, membership_tenant, managed_initial):
        if not offer_managed:
            return None
        form_kwargs = {
            'membership_tenant': membership_tenant,
            'requesting_user': self._requesting_user,
        }
        if self.is_bound:
            return ManagedRoleGrantFormSet(
                self.data, self.files,
                prefix=MANAGED_FORMSET_PREFIX, form_kwargs=form_kwargs,
            )
        return ManagedRoleGrantFormSet(
            initial=managed_initial,
            prefix=MANAGED_FORMSET_PREFIX, form_kwargs=form_kwargs,
        )

    def _technician_preset_rows(self, membership_tenant):
        """The MSP quick-onboard shape (?preset=technician): who=new + one managed
        formset row for the shared "Technician" role covering all managed tenants.

        A UI convenience only — the escalation guard still validates whatever is
        actually submitted. Name-based preselect carries NO security semantics.
        """
        self.fields['who'].initial = self.WHO_NEW
        self.fields['own_roles'].initial = []
        technician_role = Role._base_manager.filter(
            tenant=membership_tenant, shared_with_managed=True,
            name__iexact='technician', deleted_at__isnull=True,
        ).order_by('pk').first()
        if technician_role is None:
            return []
        return [{
            'role': technician_role.pk,
            'managed_scope': RoleGrantScope.SCOPE_ALL_MANAGED,
        }]

    def _layout_items(self):
        """Crispy layout for the top (non-formset) fields. The managed formset and
        the submit/cancel buttons are rendered by the template around ``{% crispy %}``."""
        items = ['tenant']
        if 'who' in self.fields:
            items.append(Fieldset(
                str(_("Who")),
                'who', 'user',
                'new_user_email', 'new_user_first_name', 'new_user_last_name',
            ))
        else:
            items.append('user')
        items.append(Fieldset(
            str(_("This organization — roles")),
            'own_roles', 'reason', 'valid_until',
        ))
        items.append('is_active')
        return items

    # --------------------------------------------------------------- validation
    def is_valid(self):
        form_valid = super().is_valid()
        formset_valid = True
        if self.managed_formset is not None:
            formset_valid = self.managed_formset.is_valid()
        return form_valid and formset_valid

    def clean(self):
        cleaned = super().clean()
        tenant = cleaned.get('tenant') or (self.instance.tenant if self.instance.pk else None)
        if tenant is None:
            raise forms.ValidationError(_("Pick the tenant this membership belongs to."))
        cleaned['tenant'] = tenant

        self._clean_who(cleaned, tenant)

        own_roles = list(cleaned.get('own_roles') or [])

        # Each own-reach role must be assignable inside this tenant.
        for role in own_roles:
            if not _role_assignable_in(role, tenant):
                raise forms.ValidationError(
                    _("Role '%(role)s' is not available in the selected tenant.") % {'role': role}
                )

        # Escalation guard — one per direct own-scope grant, aggregated so the
        # admin sees all failures. Managed-reach rows are guarded inside the formset.
        errors = []
        for role in own_roles:
            try:
                validate_role_grant(
                    self._requesting_user, role, tenant,
                    scope_type=RoleGrantScope.SCOPE_OWN,
                )
            except forms.ValidationError as exc:
                errors.extend(exc.messages)

        reactivating = bool(
            self.instance.pk
            and self._initial_is_active is False
            and cleaned.get('is_active')
        )
        if reactivating:
            # MembershipForm never edits GroupMembership rows. Switching the
            # principal back on is equivalent to adding it to every retained,
            # live group again and must pass the same inheritance guard,
            # including provider-managed projections. Inactive/deleted groups
            # remain inert and are handled if they are reactivated separately.
            retained_group_memberships = self.instance.group_memberships.filter(
                user_group__is_active=True,
                user_group__deleted_at__isnull=True,
            ).select_related('user_group')
            for group_membership in retained_group_memberships:
                try:
                    validate_group_membership_grant(
                        self._requesting_user,
                        group_membership.user_group,
                    )
                except forms.ValidationError as exc:
                    errors.extend(exc.messages)
        if errors:
            seen = set()
            raise forms.ValidationError(
                [e for e in errors if not (e in seen or seen.add(e))]
            )

        new_privileged_roles = [
            role for role in own_roles
            if role.pk not in self._existing_own_role_ids and role_is_privileged(role)
        ]
        reason = (cleaned.get('reason') or '').strip()
        valid_until = cleaned.get('valid_until')
        cleaned['reason'] = reason
        if new_privileged_roles:
            if not reason:
                self.add_error('reason', _("Elevated direct grants require a reason."))
            if valid_until is None:
                self.add_error('valid_until', _("Elevated direct grants require an expiration."))
            elif valid_until <= timezone.now():
                self.add_error('valid_until', _("The expiration must be in the future."))
        return cleaned

    def _actor_may_manage_memberships(self, tenant):
        """Whether the acting user may add/change memberships in ``tenant``.

        Superusers and an absent actor (system/programmatic contexts) are trusted;
        otherwise the relevant object-level Django permission is required. Used only
        for the membership-oracle defense in ``_clean_who`` — role-permission
        escalation is a separate, unconditional check.
        """
        user = self._requesting_user
        if user is None or getattr(user, 'is_superuser', False):
            return True
        perm = ('organization.change_membership' if self.instance.pk
                else 'organization.add_membership')
        return user.has_perm(perm, obj=tenant)

    def _clean_who(self, cleaned, tenant):
        """Enforce exactly one side of the who-radio (create only).

        The JS toggle only hides the unselected side — its inputs still POST — so
        the server clears the unselected side and requires the selected one.
        """
        self._existing_user_by_email = None
        if 'who' not in self.fields:
            return
        who = cleaned.get('who') or self.WHO_EXISTING
        cleaned['who'] = who

        if who == self.WHO_NEW:
            cleaned['user'] = None
            email = normalize_email(cleaned.get('new_user_email'))
            cleaned['new_user_email'] = email
            if not email:
                if 'new_user_email' not in self.errors:
                    self.add_error('new_user_email', _(
                        "An email address is required to create a new user."
                    ))
            else:
                try:
                    # Resolve (never create) here; the actual write is delegated to
                    # users.services on save so it is transaction-/race-safe.
                    self._existing_user_by_email = resolve_existing_user(email)
                except AmbiguousEmailError:
                    # More than one account shares this email — fail closed rather
                    # than silently picking one (email is not globally unique).
                    self._existing_user_by_email = None
                    self.add_error('new_user_email', _(
                        "More than one account already uses this email address — "
                        "resolve the duplicate before adding a membership."
                    ))
                else:
                    if self._existing_user_by_email is not None:
                        # Get-or-create semantics: reuse the account instead of
                        # duplicating it — but a second membership at the same tenant
                        # is an edit, not an add.
                        cleaned['user'] = self._existing_user_by_email
                        if Membership.objects.filter(
                            user=self._existing_user_by_email, tenant=tenant,
                        ).exists():
                            # Defense-in-depth against a membership oracle: only
                            # reveal that the account already belongs to THIS tenant
                            # to an actor allowed to manage its memberships (the
                            # create view already 404s an unauthorized deep link;
                            # this covers directly-built forms / tampered posts). An
                            # unauthorized actor gets a non-revealing error instead.
                            if self._actor_may_manage_memberships(tenant):
                                self.add_error('new_user_email', _(
                                    "%(user)s is already a member of %(tenant)s — edit "
                                    "their membership instead."
                                ) % {'user': self._existing_user_by_email, 'tenant': tenant})
                            else:
                                self.add_error('new_user_email', _(
                                    "This account cannot be added to the selected tenant."
                                ))
                    # No match → a new account is created on save with a length-safe
                    # username (users.services), so a long email / username clash is
                    # handled there rather than rejected here.
            if not (cleaned.get('new_user_first_name') or '').strip():
                self.add_error('new_user_first_name', _("Required for a new user."))
            if not (cleaned.get('new_user_last_name') or '').strip():
                self.add_error('new_user_last_name', _("Required for a new user."))
        else:
            for fname in ('new_user_email', 'new_user_first_name', 'new_user_last_name'):
                cleaned[fname] = ''
            if not cleaned.get('user'):
                self.add_error('user', _("Pick the user to add as a member."))

    def _get_validation_exclusions(self):
        exclusions = super()._get_validation_exclusions()
        if not self.instance.pk and self.cleaned_data.get('who') == self.WHO_NEW:
            # A brand-new user's row doesn't exist until save(); _clean_who has
            # already enforced the who-block (including membership uniqueness for
            # a reused account), so skip the instance-level user validation here.
            exclusions.add('user')
        return exclusions

    # ------------------------------------------------------------------ saving
    def save(self, commit=True):
        # who=new only creates a user when the email did NOT resolve to an
        # existing account in clean() (instance.user is already populated then).
        creating_new_user = (
            not self.instance.pk
            and 'who' in self.fields
            and self.cleaned_data.get('who') == self.WHO_NEW
            and self.instance.user_id is None
        )
        if creating_new_user and not commit:
            # Membership.user is a required FK, so the who-block's new user row
            # would have to be persisted NOW for the returned instance to be
            # saveable — a side effect commit=False callers don't expect. Fail
            # loudly instead of silently writing a user.
            raise ValueError(
                "MembershipForm cannot save(commit=False) while creating a new "
                "user inline. Save with commit=True, or select an existing user."
            )
        with transaction.atomic():
            if creating_new_user:
                # No user matched the email in clean(): create one inline.
                self.instance.user = self._create_inline_user()
            instance = super().save(commit=commit)
            if commit:
                # Assignments hang off a persisted membership: reconcile now.
                self._sync_grants(instance)
            else:
                # Canonical two-step (instance.save() then form.save_m2m()):
                # chain the grant reconciliation onto Django's save_m2m so the
                # deferred save writes the SAME rows a commit=True save would —
                # not a membership silently stripped of its grants.
                django_save_m2m = self.save_m2m

                def save_m2m():
                    django_save_m2m()
                    self._sync_grants(self.instance)

                self.save_m2m = save_m2m
        return instance

    def _create_inline_user(self):
        """Get-or-create the who-block's "new user" via the identity service.

        Delegates the write to ``users.services.resolve_or_create_user`` so it is
        transaction-/race-safe, length-safe (username fits the field), and reuses a
        concurrently-created account rather than duplicating it. Credentials are
        never issued automatically — the membership detail's "Send password setup
        link" action does that explicitly.
        """
        cleaned = self.cleaned_data
        user, created = resolve_or_create_user(
            email=cleaned['new_user_email'],
            first_name=cleaned.get('new_user_first_name'),
            last_name=cleaned.get('new_user_last_name'),
        )
        self.new_user_created = created
        return user

    def _sync_grants(self, membership):
        """Reconcile the membership's assignments per instance, losslessly.

        Own-reach rows follow ``own_roles``; managed-reach rows follow the formset,
        one row per grant. Surviving rows keep their ``granted_by`` provenance;
        only newly requested rows are created (stamped ``granted_by=<actor>``) and
        only removed/deselected rows are deleted (per-object ``delete()`` so each
        revocation is change-logged).
        """
        with transaction.atomic():
            self._sync_own_roles(membership)
            self._sync_managed_formset(membership)

    def _sync_own_roles(self, membership):
        selected = list(self.cleaned_data.get('own_roles') or [])
        selected_ids = {r.pk for r in selected}
        existing = list(
            membership.role_grants.filter(
                scopes__scope_type=RoleGrantScope.SCOPE_OWN,
                role__deleted_at__isnull=True,
            ).filter(
                Q(valid_until__isnull=True) | Q(valid_until__gt=timezone.now())
            ).prefetch_related('scopes').distinct()
        )
        existing_by_role = {}
        for grant in existing:
            existing_by_role.setdefault(grant.role_id, grant)
            if grant.role_id in selected_ids:
                continue
            for scope in list(grant.scopes.all()):
                if scope.scope_type == RoleGrantScope.SCOPE_OWN:
                    scope.delete()
            if not RoleGrantScope.objects.filter(role_grant=grant).exists():
                grant.delete()

        for role in selected:
            if role.pk in existing_by_role:
                continue
            privileged = role_is_privileged(role)
            grant = RoleGrant(
                membership=membership,
                role=role,
                granted_by=self._requesting_user,
                reason=self.cleaned_data.get('reason', '') if privileged else '',
                valid_until=self.cleaned_data.get('valid_until') if privileged else None,
            )
            grant.save()
            RoleGrantScope.objects.create(
                role_grant=grant,
                scope_type=RoleGrantScope.SCOPE_OWN,
            )

    def _intended_managed_rows(self, existing):
        """Pass 1: the grant rows the submitted formset intends, as
        ``(surviving_grant_or_None, role, scope, scope_group, assigned, metadata)``."""
        kept = []
        for form in self.managed_formset.forms:
            if not hasattr(form, 'cleaned_data'):
                continue
            cd = form.cleaned_data
            if cd.get('DELETE') or not cd.get('role'):
                continue
            scope = cd.get('managed_scope') or 'explicit'
            scope_group = (
                cd.get('scope_group')
                if scope == RoleGrantScope.SCOPE_TENANT_GROUP else None
            )
            assigned = (
                list(cd.get('assigned_tenants') or [])
                if scope == 'explicit' else []
            )
            raw_id = cd.get('id')
            # An id must belong to THIS membership; a stray/tampered id is ignored
            # (treated as a new row) so it can never touch another membership's grant.
            grant = existing.get(raw_id) if raw_id in existing else None
            # A role change is a revoke plus a fresh grant, never an in-place
            # mutation: granted_by/granted_at document who granted THIS role, so
            # the old row must die (a change-logged revocation via Pass 2) and a
            # new row is created under the acting user's provenance. Scope-only
            # changes still update the surviving row in place (Pass 3).
            if grant is not None and grant.role_id != cd['role'].pk:
                grant = None
            kept.append((
                grant,
                cd['role'],
                scope,
                scope_group,
                assigned,
                (cd.get('reason') or '').strip(),
                cd.get('valid_until'),
            ))
        return kept

    def _sync_managed_formset(self, membership):
        if self.managed_formset is None:
            return
        existing = {
            grant.pk: grant for grant in membership.role_grants.filter(
                role__deleted_at__isnull=True,
                scopes__scope_type__in=(
                    RoleGrantScope.SCOPE_TENANT,
                    RoleGrantScope.SCOPE_TENANT_GROUP,
                    RoleGrantScope.SCOPE_ALL_MANAGED,
                ),
            ).filter(
                Q(valid_until__isnull=True) | Q(valid_until__gt=timezone.now())
            ).prefetch_related('scopes').distinct()
        }

        # Pass 1: collect the intended rows (an existing grant or a new one).
        kept = self._intended_managed_rows(existing)

        kept_existing_ids = {
            grant.pk
            for (grant, _role, _scope, _group, _tenants, _reason, _expiry) in kept
            if grant is not None
        }

        # Pass 2: revoke every managed scope omitted by the submitted formset.
        # Preserve a possible own scope on the same aggregate.
        for pk, grant in existing.items():
            if pk not in kept_existing_ids:
                for child in list(grant.scopes.all()):
                    if child.scope_type != RoleGrantScope.SCOPE_OWN:
                        child.delete()
                if not RoleGrantScope.objects.filter(role_grant=grant).exists():
                    grant.delete()

        # Pass 3: create new aggregates and synchronize their scope children.
        for grant, role, scope, scope_group, assigned, reason, valid_until in kept:
            if grant is None:
                grant = RoleGrant(
                    membership=membership,
                    role=role,
                    granted_by=self._requesting_user,
                    reason=reason,
                    valid_until=valid_until,
                )
                grant.save()
            else:
                changed = False
                if grant.reason != reason:
                    grant.reason = reason
                    changed = True
                if grant.valid_until != valid_until:
                    grant.valid_until = valid_until
                    changed = True
                if changed:
                    grant.save(update_fields=['reason', 'valid_until'])

            if scope == RoleGrantScope.SCOPE_ALL_MANAGED:
                desired = {(RoleGrantScope.SCOPE_ALL_MANAGED, None, None)}
            elif scope == RoleGrantScope.SCOPE_TENANT_GROUP:
                desired = {(RoleGrantScope.SCOPE_TENANT_GROUP, None, scope_group.pk)}
            else:
                desired = {
                    (RoleGrantScope.SCOPE_TENANT, tenant.pk, None)
                    for tenant in assigned
                }
            current = {
                (child.scope_type, child.tenant_id, child.tenant_group_id): child
                for child in grant.scopes.all()
                if child.scope_type != RoleGrantScope.SCOPE_OWN
            }
            for key, child in current.items():
                if key not in desired:
                    child.delete()
            for scope_type, tenant_id, tenant_group_id in desired - set(current):
                RoleGrantScope.objects.create(
                    role_grant=grant,
                    scope_type=scope_type,
                    tenant_id=tenant_id,
                    tenant_group_id=tenant_group_id,
                )


class MembershipFilterForm(FilterForm):
    from ..filters import MembershipFilterSet  # inline import: breaks forms <-> filters cycle at import time
    filterset_class = MembershipFilterSet


class MembershipBulkRoleForm(BulkEditForm):
    """Bulk add/remove direct own-scope grants for selected memberships."""
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
    reason = forms.CharField(
        required=False,
        label=_("Reason for elevated direct grants"),
        widget=forms.Textarea(attrs={'rows': 2}),
    )
    valid_until = forms.DateTimeField(
        required=False,
        label=_("Expiry for elevated direct grants"),
        widget=forms.DateTimeInput(
            format='%Y-%m-%dT%H:%M',
            attrs={'type': 'datetime-local'},
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop('add_tags', None)
        self.fields.pop('remove_tags', None)

    def clean(self):
        cleaned = super().clean()
        privileged = any(
            role_is_privileged(role)
            for role in cleaned.get('roles_to_add') or []
        )
        reason = (cleaned.get('reason') or '').strip()
        valid_until = cleaned.get('valid_until')
        cleaned['reason'] = reason
        if privileged:
            if not reason:
                self.add_error('reason', _("Elevated direct grants require a reason."))
            if valid_until is None:
                self.add_error('valid_until', _("Elevated direct grants require an expiration."))
            elif valid_until <= timezone.now():
                self.add_error('valid_until', _("The expiration must be in the future."))
        return cleaned
