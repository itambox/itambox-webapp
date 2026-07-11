"""Membership form — the unified "Add member" grant flow (RBAC stage 3).

One form authors the whole grant:

  * **Who** — an existing user, or a new one created inline (get-or-create by
    email, ``set_unusable_password()``). Credentials are issued later via the
    membership detail's "Send password setup link" action — never automatically.
  * **What** — roles; the picker offers the membership tenant's own roles plus
    definitions shared down by its managing organization, labelled
    "(from <provider>)".
  * **Where** — on a managing (``is_provider``) tenant, "This organization"
    and/or "Managed tenants" (with coverage refinement). ``save()`` writes one
    own-reach and/or one managed-reach ``RoleAssignment`` row PER selected role.

Edit reconciles BOTH reaches: rows at a deselected reach are deleted, surviving
(role, reach) rows keep their ``granted_by`` provenance. Every row-to-be passes
:func:`core.auth.guards.validate_assignment_grant` in ``clean()`` and is stamped
``granted_by=<the acting user>`` on create.

``?preset=technician`` (via the ``preset`` kwarg) preselects the MSP quick-onboard
shape: new user, managed reach over all managed tenants, and the shared
"Technician" role when one exists.
"""
from django import forms
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Fieldset, Div

from core.forms import FilterForm, BulkEditForm
from core.auth.guards import validate_assignment_grant
from organization.access import get_descendant_tenant_group_ids
from .helpers import add_standard_buttons
from ..models import Membership, Role, RoleAssignment, Tenant, TenantGroup

User = get_user_model()


class _RolePickerField(forms.ModelMultipleChoiceField):
    """Role picker that labels shared-in definitions with their provider.

    ``membership_tenant`` is assigned per form instance (fields are deep-copied,
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


class MembershipForm(forms.ModelForm):
    """ModelForm for ``organization.Membership`` — the unified grant flow.

    Who / What / Where sections (see module docstring). The Who block only
    exists on create (user is immutable on edit); the Where block only renders
    when the membership's tenant is a managing (``is_provider``) tenant — or is
    not yet known (context-free create), in which case ``clean()`` re-validates
    against the tenant actually posted.
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
    roles = _RolePickerField(
        queryset=Role._base_manager.none(), required=False, label=_("Roles"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
        help_text=_("This tenant's roles, plus definitions shared down by its "
                    "managing organization."),
    )
    reach_own = forms.BooleanField(
        required=False, initial=True, label=_("This organization"),
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text=_("The selected roles apply inside this tenant."),
    )
    reach_managed = forms.BooleanField(
        required=False, initial=False, label=_("Managed tenants"),
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text=_("The selected roles additionally reach into the tenants this "
                    "organization manages."),
    )
    managed_scope = forms.ChoiceField(
        choices=RoleAssignment.SCOPE_CHOICES,
        initial=RoleAssignment.SCOPE_EXPLICIT,
        required=False, label=_("Coverage"),
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
        self._preset = kwargs.pop('preset', None)
        super().__init__(*args, **kwargs)

        #: Set by save() when the who-block created a brand-new user, so the view
        #: can surface the "send password setup link" hint.
        self.new_user_created = False
        self._existing_user_by_email = None

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

        # Who block only exists on create; on edit the user is immutable.
        if self.instance.pk:
            for fname in ('who', 'new_user_email', 'new_user_first_name',
                          'new_user_last_name'):
                self.fields.pop(fname, None)

        # Role picker: the tenant's own roles plus roles shared down by its managing
        # organization. Unknown tenant (context-free GET) falls back to all roles;
        # clean() re-validates ownership against the tenant actually submitted.
        role_qs = Role._base_manager.filter(
            deleted_at__isnull=True).select_related('tenant')
        if membership_tenant is not None:
            ownership = Q(tenant=membership_tenant)
            if membership_tenant.managed_by_id:
                ownership |= Q(
                    tenant_id=membership_tenant.managed_by_id, shared_with_managed=True,
                )
            role_qs = role_qs.filter(ownership)
        self.fields['roles'].queryset = role_qs.order_by('name')
        self.fields['roles'].membership_tenant = membership_tenant

        # The Where block (reach checkboxes + refinement) only exists on managing
        # (is_provider) tenants: elsewhere everything is own reach implicitly.
        offer_managed = membership_tenant is None or membership_tenant.is_provider
        if not offer_managed:
            for fname in ('reach_own', 'reach_managed', 'managed_scope',
                          'scope_group', 'assigned_tenants'):
                self.fields.pop(fname, None)
        elif membership_tenant is not None:
            self.fields['assigned_tenants'].queryset = Tenant._base_manager.filter(
                managed_by=membership_tenant, deleted_at__isnull=True,
            ).order_by('name')

        # Edit: seed roles from BOTH reaches; the reach checkboxes reflect which
        # reaches currently carry rows, refinement from the first managed row.
        if self.instance.pk:
            own_role_ids = set(
                self.instance.assignments.filter(
                    reach=RoleAssignment.REACH_OWN,
                ).values_list('role_id', flat=True)
            )
            managed_rows = list(
                self.instance.assignments.filter(
                    reach=RoleAssignment.REACH_MANAGED,
                ).select_related('scope_group')
            )
            self.fields['roles'].initial = sorted(
                own_role_ids | {a.role_id for a in managed_rows}
            )
            if 'reach_own' in self.fields:
                self.fields['reach_own'].initial = bool(own_role_ids)
                self.fields['reach_managed'].initial = bool(managed_rows)
                if managed_rows:
                    first = managed_rows[0]
                    self.fields['managed_scope'].initial = (
                        first.managed_scope or RoleAssignment.SCOPE_EXPLICIT
                    )
                    self.fields['scope_group'].initial = first.scope_group_id
                    self.fields['assigned_tenants'].initial = list(
                        first.assigned_tenants.values_list('pk', flat=True)
                    )
        elif not self.is_bound and self._preset == self.PRESET_TECHNICIAN \
                and membership_tenant is not None and membership_tenant.is_provider:
            self._apply_technician_preset(membership_tenant)

        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(*self._layout_items())
        add_standard_buttons(self.helper, self.instance, 'organization:membership_list')

    def _apply_technician_preset(self, membership_tenant):
        """Preselect the MSP quick-onboard shape (?preset=technician).

        A UI convenience only: it merely sets initials — the escalation guard in
        ``clean()`` still validates whatever ends up submitted. Mirrors the old
        TechnicianQuick flow: a NEW user with a managed-reach grant over all
        managed tenants ("This organization" deliberately unchecked).
        """
        self.fields['who'].initial = self.WHO_NEW
        self.fields['reach_own'].initial = False
        self.fields['reach_managed'].initial = True
        self.fields['managed_scope'].initial = RoleAssignment.SCOPE_ALL
        # Name-based preselect of the conventionally-named shared role. This carries
        # NO security semantics (unlike a magic-string backdoor) — it only pre-checks
        # a picker option the admin could click by hand.
        technician_role = Role._base_manager.filter(
            tenant=membership_tenant, shared_with_managed=True,
            name__iexact='technician', deleted_at__isnull=True,
        ).order_by('pk').first()
        if technician_role is not None:
            self.fields['roles'].initial = [technician_role.pk]

    def _layout_items(self):
        """Crispy layout: tenant, then Who / What / Where sections, then active."""
        items = ['tenant']
        if 'who' in self.fields:
            items.append(Fieldset(
                str(_("Who")),
                'who', 'user',
                'new_user_email', 'new_user_first_name', 'new_user_last_name',
            ))
        else:
            items.append('user')
        items.append(Fieldset(str(_("What — roles")), 'roles'))
        if 'reach_managed' in self.fields:
            items.append(Fieldset(
                str(_("Where — coverage")),
                'reach_own', 'reach_managed',
                Div(
                    'managed_scope', 'scope_group', 'assigned_tenants',
                    css_class='managed-refinement ps-4',
                ),
            ))
        items.append('is_active')
        return items

    # ------------------------------------------------------------------ cleaning
    def clean(self):
        cleaned = super().clean()
        tenant = cleaned.get('tenant') or (self.instance.tenant if self.instance.pk else None)
        if tenant is None:
            raise forms.ValidationError(_("Pick the tenant this membership belongs to."))
        cleaned['tenant'] = tenant

        self._clean_who(cleaned, tenant)

        roles = list(cleaned.get('roles') or [])

        # The Where block collapses to own reach when it isn't offered (non-provider
        # tenant). A tampered POST re-adding managed reach is caught below because
        # the popped fields never reach cleaned_data.
        has_where = 'reach_managed' in self.fields
        reach_own = bool(cleaned.get('reach_own')) if has_where else True
        reach_managed = bool(cleaned.get('reach_managed')) if has_where else False
        cleaned['reach_own'] = reach_own
        cleaned['reach_managed'] = reach_managed

        if roles and not reach_own and not reach_managed:
            self.add_error('reach_own', _(
                "Pick where the selected roles apply: this organization, its "
                "managed tenants, or both."
            ))
            return cleaned

        requested_tenant_ids = None
        if reach_managed:
            if not tenant.is_provider:
                self.add_error('reach_managed', _(
                    "Reach into managed tenants requires a tenant that manages others."
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

        # Escalation guards — one per RoleAssignment row this form is about to
        # write (role × selected reach), aggregated so the admin sees all failures.
        grants = []
        if reach_own:
            grants.append((RoleAssignment.REACH_OWN, None))
        if reach_managed:
            grants.append((RoleAssignment.REACH_MANAGED, requested_tenant_ids))
        errors = []
        for role in roles:
            for reach, req_ids in grants:
                try:
                    validate_assignment_grant(
                        self._requesting_user, role, tenant,
                        reach=reach, requested_tenant_ids=req_ids,
                    )
                except forms.ValidationError as exc:
                    errors.extend(exc.messages)
        if errors:
            seen = set()
            raise forms.ValidationError(
                [e for e in errors if not (e in seen or seen.add(e))]
            )
        return cleaned

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
            email = (cleaned.get('new_user_email') or '').strip().lower()
            cleaned['new_user_email'] = email
            if not email:
                if 'new_user_email' not in self.errors:
                    self.add_error('new_user_email', _(
                        "An email address is required to create a new user."
                    ))
            else:
                self._existing_user_by_email = User.objects.filter(
                    email__iexact=email,
                ).order_by('pk').first()
                if self._existing_user_by_email is not None:
                    # Get-or-create semantics: reuse the account instead of
                    # duplicating it — but a second membership at the same tenant
                    # is an edit, not an add.
                    cleaned['user'] = self._existing_user_by_email
                    if Membership.objects.filter(
                        user=self._existing_user_by_email, tenant=tenant,
                    ).exists():
                        self.add_error('new_user_email', _(
                            "%(user)s is already a member of %(tenant)s — edit "
                            "their membership instead."
                        ) % {'user': self._existing_user_by_email, 'tenant': tenant})
                elif User.objects.filter(username=email).exists():
                    # username=email convention: a stale account with that username
                    # but a different email would make the insert 500 otherwise.
                    self.add_error('new_user_email', _(
                        "A user with this username already exists but uses a "
                        "different email address."
                    ))
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
        creating_new_user = (
            not self.instance.pk
            and 'who' in self.fields
            and self.cleaned_data.get('who') == self.WHO_NEW
        )
        with transaction.atomic():
            if creating_new_user and self.instance.user_id is None:
                # No user matched the email in clean(): create one inline.
                self.instance.user = self._create_inline_user()
            instance = super().save(commit=commit)
            if commit:
                self._sync_assignments(instance)
        return instance

    def _create_inline_user(self):
        """Create the who-block's "new user": username=email, unusable password.

        Credentials are never issued automatically — the membership detail's
        "Send password setup link" action does that explicitly.
        """
        cleaned = self.cleaned_data
        email = cleaned['new_user_email']
        user = User(
            username=email,
            email=email,
            first_name=(cleaned.get('new_user_first_name') or '').strip(),
            last_name=(cleaned.get('new_user_last_name') or '').strip(),
            is_active=True,
        )
        user.set_unusable_password()
        user.save()
        self.new_user_created = True
        return user

    def _sync_assignments(self, membership):
        """Reconcile the membership's assignments at BOTH reaches.

        For each reach, the selected roles are exactly the roles that keep (or
        gain) a row there; everything else at that reach is deleted — including
        every row of a reach whose checkbox was cleared. Untouched rows keep
        their ``granted_by`` provenance. Deletes go through per-object
        ``delete()`` so change logging records each revocation.
        """
        cleaned = self.cleaned_data
        roles = list(cleaned.get('roles') or [])
        reach_plan = {
            RoleAssignment.REACH_OWN: roles if cleaned.get('reach_own') else [],
            RoleAssignment.REACH_MANAGED: roles if cleaned.get('reach_managed') else [],
        }

        with transaction.atomic():
            for reach, selected in reach_plan.items():
                stale = membership.assignments.filter(reach=reach)
                if selected:
                    stale = stale.exclude(role__in=selected)
                for assignment in stale:
                    assignment.delete()

            for role in reach_plan[RoleAssignment.REACH_OWN]:
                RoleAssignment.objects.get_or_create(
                    membership=membership, role=role, reach=RoleAssignment.REACH_OWN,
                    defaults={'granted_by': self._requesting_user},
                )

            managed_roles = reach_plan[RoleAssignment.REACH_MANAGED]
            if managed_roles:
                managed_scope = cleaned.get('managed_scope') or RoleAssignment.SCOPE_EXPLICIT
                scope_group = cleaned.get('scope_group')
                assigned_tenants = list(cleaned.get('assigned_tenants') or [])
                for role in managed_roles:
                    assignment, created = RoleAssignment.objects.get_or_create(
                        membership=membership, role=role,
                        reach=RoleAssignment.REACH_MANAGED,
                        defaults={
                            'managed_scope': managed_scope,
                            'scope_group': scope_group,
                            'granted_by': self._requesting_user,
                        },
                    )
                    if not created and (
                        assignment.managed_scope != managed_scope
                        or assignment.scope_group_id != getattr(scope_group, 'pk', None)
                    ):
                        assignment.managed_scope = managed_scope
                        assignment.scope_group = scope_group
                        assignment.save()
                    assignment.assigned_tenants.set(
                        assigned_tenants
                        if managed_scope == RoleAssignment.SCOPE_EXPLICIT else []
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
