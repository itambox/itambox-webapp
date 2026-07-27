"""Unified Membership and canonical RoleGrant editor.

Own-tenant roles use direct grants with an ``own`` scope. Provider reach uses one
direct grant plus additive RoleGrantScope children. Every elevated direct grant
requires a reason and future expiration.
"""

from crispy_forms.helper import FormHelper
from crispy_forms.layout import Fieldset, Layout
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import NON_FIELD_ERRORS
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.forms import BulkEditForm, FilterForm
from core.mfa import role_is_privileged
from organization.services.errors import MembershipServiceError
from organization.services.membership import (
    MembershipIntent,
    NewIdentitySpec,
    apply_membership_grants,
    execute_membership_write,
    may_manage_memberships,
    plan_membership_write,
)
from organization.services.rolegrants import (
    SCOPE_EXPLICIT,
    GrantPlan,
    ManagedGrantSpec,
    OwnGrantSpec,
    assignable_roles_qs,
    live_managed_grants,
    live_own_grants,
    managed_target_tenants_qs,
)
from users.services import AmbiguousEmailError, normalize_email, resolve_existing_user

from ..models import Membership, Role, RoleGrantScope, Tenant, TenantGroup

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
                "role": role.name,
                "provider": role.tenant.name,
            }
        return role.name


class _RolePickerField(_RoleLabelMixin, forms.ModelMultipleChoiceField):
    """Multi-select role picker (own-reach roles)."""


class _RoleChoiceField(_RoleLabelMixin, forms.ModelChoiceField):
    """Single-select role picker (one managed-grant formset row)."""


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
        queryset=Role._base_manager.none(),
        required=False,
        label=_("Role"),
        widget=forms.Select(attrs={"class": "form-select managed-role"}),
    )
    managed_scope = forms.ChoiceField(
        choices=(
            (SCOPE_EXPLICIT, _("Specific tenants")),
            (RoleGrantScope.SCOPE_TENANT_GROUP, _("A tenant group + its descendants")),
            (RoleGrantScope.SCOPE_ALL_MANAGED, _("All managed tenants")),
        ),
        initial=SCOPE_EXPLICIT,
        required=False,
        label=_("Coverage"),
        widget=forms.Select(attrs={"class": "form-select managed-scope"}),
    )
    scope_group = forms.ModelChoiceField(
        queryset=TenantGroup._base_manager.none(),
        required=False,
        label=_("Tenant group"),
        widget=forms.Select(attrs={"class": "form-select managed-scope-group"}),
    )
    assigned_tenants = forms.ModelMultipleChoiceField(
        queryset=Tenant._base_manager.none(),
        required=False,
        label=_("Specific tenants"),
        widget=forms.SelectMultiple(attrs={"class": "form-select managed-assigned-tenants"}),
    )
    reason = forms.CharField(
        required=False,
        label=_("Reason"),
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=_("Required when this is an elevated direct grant."),
    )
    valid_until = forms.DateTimeField(
        required=False,
        label=_("Valid until"),
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
        help_text=_("Required and must be in the future for elevated direct grants."),
    )

    def __init__(self, *args, membership_tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        # The tenant shapes the row's querysets and labels only; it carries no
        # authorization weight and the actor is deliberately not kept here —
        # every decision about this row is taken by validate_grant_plan.
        self.fields["role"].queryset = assignable_roles_qs(membership_tenant)
        self.fields["role"].membership_tenant = membership_tenant
        self.fields["scope_group"].queryset = TenantGroup._base_manager.filter(deleted_at__isnull=True).order_by("name")
        self.fields["assigned_tenants"].queryset = managed_target_tenants_qs(membership_tenant)

    def is_blank(self):
        """A row the user never touched (no role selected) is ignored, not errored."""
        return not (self.cleaned_data.get("role") if hasattr(self, "cleaned_data") else None)

    def clean(self):
        """Shape and required-ness only — every domain rule lives in the plan.

        Coverage normalisation stays here because the *widget* contract owns it:
        the JS toggle leaves the unselected coverage inputs in the POST, so the
        server clears the side the user did not choose before anything reads the
        row. Assignability, provider reach, the elevated-grant policy and the
        escalation guard are decided by
        ``organization.services.rolegrants.validate_grant_plan`` and reported
        back onto this row by ``MembershipForm._add_service_errors``.
        """
        cleaned = super().clean()
        # Deleted or entirely-blank rows carry no grant and are skipped.
        if cleaned.get("DELETE"):
            return cleaned
        if not cleaned.get("role"):
            return cleaned

        scope = cleaned.get("managed_scope") or SCOPE_EXPLICIT
        cleaned["managed_scope"] = scope
        if scope == RoleGrantScope.SCOPE_TENANT_GROUP:
            cleaned["assigned_tenants"] = []
            if not cleaned.get("scope_group"):
                self.add_error(
                    "scope_group", _("A tenant group is required when coverage is 'A tenant group + its descendants'.")
                )
        elif scope == SCOPE_EXPLICIT:
            cleaned["scope_group"] = None
            if not cleaned.get("assigned_tenants"):
                self.add_error("assigned_tenants", _("Pick at least one tenant for 'Specific tenants'."))
        else:  # SCOPE_ALL_MANAGED carries no explicit target of either kind.
            cleaned["scope_group"] = None
            cleaned["assigned_tenants"] = []
        cleaned["reason"] = (cleaned.get("reason") or "").strip()
        return cleaned


class BaseManagedRoleGrantFormSet(forms.BaseFormSet):
    """Plain formset.

    The "one row per role" rule used to live here and could only be reported as
    a formset-wide non-form error. It is now a plan check
    (``validate_grant_plan``), which knows the offending row and reports it
    there — so this class keeps only the base behaviour and exists to document
    where the rule went.
    """


ManagedRoleGrantFormSet = forms.formset_factory(
    ManagedRoleGrantForm,
    formset=BaseManagedRoleGrantFormSet,
    extra=1,
    can_delete=True,
)

MANAGED_FORMSET_PREFIX = "managed"


class MembershipForm(forms.ModelForm):
    """ModelForm for ``organization.Membership`` — the unified, lossless grant flow.

    Who / This-organization / Managed-tenants sections (see module docstring). The
    Who block only exists on create (user is immutable on edit); the Managed block
    only renders when the membership's tenant is a managing (``is_provider``)
    tenant — or is not yet known (context-free create), in which case ``clean()``
    and the formset re-validate against the tenant actually posted.
    """

    WHO_EXISTING = "existing"
    WHO_NEW = "new"
    WHO_CHOICES = [
        (WHO_EXISTING, _("Existing user")),
        (WHO_NEW, _("New user")),
    ]

    PRESET_TECHNICIAN = "technician"

    who = forms.ChoiceField(
        choices=WHO_CHOICES,
        initial=WHO_EXISTING,
        required=False,
        label=_("Who"),
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )
    user = forms.ModelChoiceField(
        queryset=User.objects.all(),
        required=False,
        label=_("User"),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    new_user_email = forms.EmailField(
        required=False,
        label=_("Email"),
        widget=forms.EmailInput(
            attrs={
                "class": "form-control",
                "placeholder": "person@example.com",
            }
        ),
        help_text=_(
            "An existing account with this email is reused; otherwise a new "
            "user without a password is created — send them a password setup "
            "link afterwards."
        ),
    )
    new_user_first_name = forms.CharField(
        max_length=150,
        required=False,
        label=_("First name"),
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    new_user_last_name = forms.CharField(
        max_length=150,
        required=False,
        label=_("Last name"),
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    own_roles = _RolePickerField(
        queryset=Role._base_manager.none(),
        required=False,
        label=_("Roles"),
        widget=forms.SelectMultiple(attrs={"class": "form-select"}),
        help_text=_(
            "Roles that apply inside this organization. This tenant's roles, "
            "plus definitions shared down by its managing organization."
        ),
    )
    reason = forms.CharField(
        required=False,
        label=_("Reason for new elevated direct grants"),
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=_("Required when adding an elevated role directly to this membership."),
    )
    valid_until = forms.DateTimeField(
        required=False,
        label=_("Expiry for new elevated direct grants"),
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
        help_text=_("Required and must be in the future for new elevated direct grants."),
    )

    class Meta:
        model = Membership
        fields = ["user", "tenant", "is_active"]
        widgets = {
            "tenant": forms.Select(attrs={"class": "form-select"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        self._requesting_user = kwargs.pop("user", None)
        self._tenant_ctx = kwargs.pop("tenant", None)
        self._preset = kwargs.pop("preset", None)
        super().__init__(*args, **kwargs)
        # Keep the persisted state before ModelForm applies submitted values to
        # ``instance``. Reactivation restores both direct grants and every group
        # grant inherited through this Membership.
        self._initial_is_active = self.instance.is_active if self.instance.pk else None

        #: Set by save() when the who-block created a brand-new user, so the view
        #: can surface the "send password setup link" hint.
        self.new_user_created = False
        self._existing_user_by_email = None

        self.fields["user"].queryset = User.objects.order_by("username")

        # Cross-tenant pickers must use the unscoped base manager so they're not
        # silently emptied by the active-tenant form-field scoping in core.apps.
        self.fields["tenant"].queryset = Tenant._base_manager.filter(deleted_at__isnull=True).order_by("name")

        # Resolve the membership's tenant: locked on edit, prefilled from context on
        # create, otherwise (context-free create) recovered from POST data so the
        # dependent querysets/choices are built against the tenant being submitted.
        membership_tenant = None
        if self.instance.pk:
            membership_tenant = self.instance.tenant
            self.fields["tenant"].queryset = Tenant._base_manager.filter(pk=self.instance.tenant_id)
            self.fields["tenant"].initial = self.instance.tenant_id
            self.fields["tenant"].disabled = True
            self.fields["user"].disabled = True
        elif self._tenant_ctx is not None:
            membership_tenant = self._tenant_ctx
            self.fields["tenant"].queryset = Tenant._base_manager.filter(pk=self._tenant_ctx.pk)
            self.fields["tenant"].initial = membership_tenant.pk
            self.fields["tenant"].widget = forms.HiddenInput()
        elif self.is_bound:
            try:
                membership_tenant = Tenant._base_manager.filter(
                    pk=self.data.get("tenant"),
                    deleted_at__isnull=True,
                ).first()
            except (TypeError, ValueError):  # non-numeric tenant id must not 500
                membership_tenant = None
        self._membership_tenant = membership_tenant

        # Who block only exists on create; on edit the user is immutable.
        if self.instance.pk:
            for fname in ("who", "new_user_email", "new_user_first_name", "new_user_last_name"):
                self.fields.pop(fname, None)

        # own_roles: the tenant's own roles plus roles shared down by its managing
        # organization. Unknown tenant (context-free GET) falls back to all roles.
        self.fields["own_roles"].queryset = assignable_roles_qs(membership_tenant)
        self.fields["own_roles"].membership_tenant = membership_tenant

        # The Managed block (per-grant formset) only exists on managing
        # (is_provider) tenants: elsewhere every grant is own reach implicitly.
        offer_managed = membership_tenant is None or membership_tenant.is_provider

        # Seed own_roles + the managed formset losslessly from the existing rows.
        managed_initial = []
        self._existing_own_role_ids = set()
        if self.instance.pk:
            # INV-10 (expired grants are inert audit history, soft-deleted roles
            # are not offered) is defined once, in the service: these are the
            # very reads the write phase and its tamper check use, so the editor
            # cannot show a selection the reconciler would disagree about.
            self._existing_own_role_ids = set(live_own_grants(self.instance).values_list("role_id", flat=True))
            self.fields["own_roles"].initial = sorted(self._existing_own_role_ids)
            for grant in live_managed_grants(self.instance):
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
                        scope = SCOPE_EXPLICIT
                        scope_group_id = None
                        tenant_ids = [
                            s.tenant_id for s in scopes if s.scope_type == RoleGrantScope.SCOPE_TENANT and s.tenant_id
                        ]
                managed_initial.append(
                    {
                        "id": grant.pk,
                        "role": grant.role_id,
                        "managed_scope": scope,
                        "scope_group": scope_group_id,
                        "assigned_tenants": tenant_ids,
                        "reason": grant.reason,
                        "valid_until": grant.valid_until,
                    }
                )
        elif (
            not self.is_bound
            and self._preset == self.PRESET_TECHNICIAN
            and membership_tenant is not None
            and membership_tenant.is_provider
        ):
            managed_initial = self._technician_preset_rows(membership_tenant)

        self.managed_formset = self._build_managed_formset(
            offer_managed,
            membership_tenant,
            managed_initial,
        )

        self.helper = FormHelper(self)
        self.helper.form_tag = False  # the template wraps the <form> so it can embed the formset
        self.helper.disable_csrf = True
        self.helper.layout = Layout(*self._layout_items())

    def _build_managed_formset(self, offer_managed, membership_tenant, managed_initial):
        if not offer_managed:
            return None
        form_kwargs = {"membership_tenant": membership_tenant}
        if self.is_bound:
            return ManagedRoleGrantFormSet(
                self.data,
                self.files,
                prefix=MANAGED_FORMSET_PREFIX,
                form_kwargs=form_kwargs,
            )
        return ManagedRoleGrantFormSet(
            initial=managed_initial,
            prefix=MANAGED_FORMSET_PREFIX,
            form_kwargs=form_kwargs,
        )

    def _technician_preset_rows(self, membership_tenant):
        """The MSP quick-onboard shape (?preset=technician): who=new + one managed
        formset row for the shared "Technician" role covering all managed tenants.

        A UI convenience only — the escalation guard still validates whatever is
        actually submitted. Name-based preselect carries NO security semantics.
        """
        self.fields["who"].initial = self.WHO_NEW
        self.fields["own_roles"].initial = []
        technician_role = (
            Role._base_manager.filter(
                tenant=membership_tenant,
                shared_with_managed=True,
                name__iexact="technician",
                deleted_at__isnull=True,
            )
            .order_by("pk")
            .first()
        )
        if technician_role is None:
            return []
        return [
            {
                "role": technician_role.pk,
                "managed_scope": RoleGrantScope.SCOPE_ALL_MANAGED,
            }
        ]

    def _layout_items(self):
        """Crispy layout for the top (non-formset) fields. The managed formset and
        the submit/cancel buttons are rendered by the template around ``{% crispy %}``."""
        items = ["tenant"]
        if "who" in self.fields:
            items.append(
                Fieldset(
                    str(_("Who")),
                    "who",
                    "user",
                    "new_user_email",
                    "new_user_first_name",
                    "new_user_last_name",
                )
            )
        else:
            items.append("user")
        items.append(
            Fieldset(
                str(_("This organization — roles")),
                "own_roles",
                "reason",
                "valid_until",
            )
        )
        items.append("is_active")
        return items

    # --------------------------------------------------------------- validation
    def is_valid(self):
        form_valid = super().is_valid()
        formset_valid = True
        if self.managed_formset is not None:
            formset_valid = self.managed_formset.is_valid()
        return form_valid and formset_valid

    def clean(self):
        """UI rules, then one read-only plan.

        Planning here is for ERROR REPORTING ONLY: ``save()`` re-plans inside the
        transaction, after the row lock, so nothing computed here is carried into
        the write and the plan is deliberately not retained on the form.
        """
        cleaned = super().clean()
        tenant = cleaned.get("tenant") or (self.instance.tenant if self.instance.pk else None)
        if tenant is None:
            raise forms.ValidationError(_("Pick the tenant this membership belongs to."))
        cleaned["tenant"] = tenant

        # Field-level UI rules first, so their precise messages always exist even
        # when the service also rejects the write (this is what keeps the
        # membership-oracle message on ``new_user_email``).
        self._clean_who(cleaned, tenant)
        cleaned["reason"] = (cleaned.get("reason") or "").strip()

        # The formset must be cleaned before its rows can be read. The expensive
        # part -- full_clean() -- runs once behind the cached ``errors``
        # property, so the later call in is_valid() only re-reads per-form errors.
        if self.managed_formset is not None:
            self.managed_formset.is_valid()

        try:
            plan_membership_write(
                actor=self._requesting_user,
                intent=self._build_intent(cleaned, tenant),
                membership=self.instance if self.instance.pk else None,
                # INV-14. Derived from the row as LOADED, never from the POST.
                # execute_membership_write re-derives it from the locked row and
                # that derivation is the authoritative one; this one exists so a
                # blocked reactivation renders as a form error instead of a 500.
                revalidate_inherited_groups=bool(
                    self.instance.pk and self._initial_is_active is False and cleaned.get("is_active")
                ),
            )
        except MembershipServiceError as exc:
            self._add_service_errors(exc)
        return cleaned

    @staticmethod
    def _already_reported(form, field, message):
        """Whether ``form`` already shows ``message`` on that exact target.

        Per target, not per form: the identical sentence on two different rows
        is two distinct locations and both must survive — the same rule the
        service's own ``(message, field, row_index)`` de-duplication follows.
        """
        return message in form.errors.get(field or NON_FIELD_ERRORS, [])

    def _add_service_errors(self, exc):
        """Route each rejection back to the field or row it names.

        ``row_index`` is an index into ``managed_formset.forms`` (§4.2). An index
        this form cannot render, or a field name it does not carry, degrades to a
        form-level error rather than raising ``IndexError``/``ValueError`` in the
        middle of validation.

        A message the target already carries is skipped. This only ever ADDS to
        errors the form's own field-level rules produced first (that ordering is
        what keeps the non-revealing message on ``new_user_email``), so without
        the check the same sentence can render twice under one label.
        """
        rows = self.managed_formset.forms if self.managed_formset is not None else ()
        for err in exc.errors:
            if err.row_index is not None and 0 <= err.row_index < len(rows):
                row = rows[err.row_index]
                field = err.field if err.field in row.fields else None
                if not self._already_reported(row, field, err.message):
                    row.add_error(field, err.message)
                continue
            field = err.field if err.row_index is None and err.field in self.fields else None
            if not self._already_reported(self, field, err.message):
                self.add_error(field, err.message)

    def _clean_who(self, cleaned, tenant):
        """Enforce exactly one side of the who-radio (create only).

        The JS toggle only hides the unselected side — its inputs still POST — so
        the server clears the unselected side and requires the selected one.
        """
        self._existing_user_by_email = None
        if "who" not in self.fields:
            return
        who = cleaned.get("who") or self.WHO_EXISTING
        cleaned["who"] = who

        if who == self.WHO_NEW:
            self._clean_who_new(cleaned, tenant)
        else:
            self._clean_who_existing(cleaned)

    def _clean_who_new(self, cleaned, tenant):
        """The "new user" side of the who-radio: resolve-or-create by email."""
        cleaned["user"] = None
        email = normalize_email(cleaned.get("new_user_email"))
        cleaned["new_user_email"] = email
        # Authorization must short-circuit before email lookup or membership
        # probing: either operation can disclose account/tenant state.
        if not may_manage_memberships(
            actor=self._requesting_user,
            tenant=tenant,
            creating=not bool(self.instance.pk),
        ):
            self.add_error(
                "new_user_email",
                _("This account cannot be added to the selected tenant."),
            )
            return
        if not email:
            if "new_user_email" not in self.errors:
                self.add_error("new_user_email", _("An email address is required to create a new user."))
        else:
            self._resolve_new_user_by_email(cleaned, tenant, email)
        if not (cleaned.get("new_user_first_name") or "").strip():
            self.add_error("new_user_first_name", _("Required for a new user."))
        if not (cleaned.get("new_user_last_name") or "").strip():
            self.add_error("new_user_last_name", _("Required for a new user."))

    def _resolve_new_user_by_email(self, cleaned, tenant, email):
        """Look up ``email`` and, on a single match, apply get-or-create semantics."""
        try:
            # Resolve (never create) here; the actual write is delegated to
            # users.services on save so it is transaction-/race-safe.
            self._existing_user_by_email = resolve_existing_user(email)
        except AmbiguousEmailError:
            # More than one account shares this email — fail closed rather
            # than silently picking one (email is not globally unique).
            self._existing_user_by_email = None
            self.add_error(
                "new_user_email",
                _(
                    "More than one account already uses this email address — "
                    "resolve the duplicate before adding a membership."
                ),
            )
            return
        if self._existing_user_by_email is None:
            # No match → a new account is created on save with a length-safe
            # username (users.services), so a long email / username clash is
            # handled there rather than rejected here.
            return
        # Get-or-create semantics: reuse the account instead of duplicating it —
        # but a second membership at the same tenant is an edit, not an add.
        cleaned["user"] = self._existing_user_by_email
        self._check_email_reuse_conflict(tenant)

    def _check_email_reuse_conflict(self, tenant):
        """A reused account already a member of ``tenant`` is an edit, not an add."""
        if not Membership.objects.filter(user=self._existing_user_by_email, tenant=tenant).exists():
            return
        # Defense-in-depth against a membership oracle: only reveal that the
        # account already belongs to THIS tenant to an actor allowed to manage
        # its memberships (the create view already 404s an unauthorized deep
        # link; this covers directly-built forms / tampered posts). An
        # unauthorized actor gets a non-revealing error instead.
        if may_manage_memberships(
            actor=self._requesting_user,
            tenant=tenant,
            creating=not bool(self.instance.pk),
        ):
            self.add_error(
                "new_user_email",
                _("%(user)s is already a member of %(tenant)s — edit their membership instead.")
                % {"user": self._existing_user_by_email, "tenant": tenant},
            )
        else:
            self.add_error("new_user_email", _("This account cannot be added to the selected tenant."))

    def _clean_who_existing(self, cleaned):
        """The "existing user" side of the who-radio: clear the new-user fields."""
        for fname in ("new_user_email", "new_user_first_name", "new_user_last_name"):
            cleaned[fname] = ""
        if not cleaned.get("user"):
            self.add_error("user", _("Pick the user to add as a member."))

    def _get_validation_exclusions(self):
        exclusions = super()._get_validation_exclusions()
        if not self.instance.pk and self.cleaned_data.get("who") == self.WHO_NEW:
            # A brand-new user's row doesn't exist until save(); _clean_who has
            # already enforced the who-block (including membership uniqueness for
            # a reused account), so skip the instance-level user validation here.
            exclusions.add("user")
        return exclusions

    # ---------------------------------------------------- service input objects
    def _own_specs(self, cleaned):
        """The own-reach half of the intent.

        Every selected role carries the single main-form reason/expiry pair; the
        service applies INV-5's own-half gate (metadata only on a newly created
        grant, and only when the role is privileged).
        """
        reason = (cleaned.get("reason") or "").strip()
        valid_until = cleaned.get("valid_until")
        return tuple(
            OwnGrantSpec(role=role, reason=reason, valid_until=valid_until) for role in cleaned.get("own_roles") or []
        )

    def _managed_specs(self):
        """The managed-reach half, one spec per submitted formset row.

        Three kinds of row are skipped, exactly as the reconciler skipped them
        before: rows the formset never cleaned, deleted/blank rows, and rows that
        already carry a field-level error. The last matters because
        ``add_error`` deletes the offending key from ``cleaned_data``: an
        explicit row that failed *"Pick at least one tenant"* would otherwise
        reach the plan as ``tenants=()`` and collect a second, duplicate message
        on the same field. ``row_index`` stays the index into
        ``managed_formset.forms`` regardless of how many rows were skipped, so a
        service error can always be rendered on the row it came from.
        """
        if self.managed_formset is None:
            return ()
        specs = []
        for index, row in enumerate(self.managed_formset.forms):
            if not hasattr(row, "cleaned_data"):
                continue
            cd = row.cleaned_data
            if cd.get("DELETE") or not cd.get("role"):
                continue
            if row.errors:
                continue
            scope = cd.get("managed_scope") or SCOPE_EXPLICIT
            specs.append(
                ManagedGrantSpec(
                    role=cd["role"],
                    scope=scope,
                    grant_id=cd.get("id"),
                    scope_group=cd.get("scope_group") if scope == RoleGrantScope.SCOPE_TENANT_GROUP else None,
                    tenants=tuple(cd.get("assigned_tenants") or []) if scope == SCOPE_EXPLICIT else (),
                    reason=(cd.get("reason") or "").strip(),
                    valid_until=cd.get("valid_until"),
                    row_index=index,
                )
            )
        return tuple(specs)

    def _grant_plan(self, cleaned):
        """The grant half of the intent, shared by ``save(commit=True)``'s
        ``MembershipIntent`` and the deferred ``save_m2m`` path so there is only
        ever one translation from form data to service input."""
        return GrantPlan(own=self._own_specs(cleaned), managed=self._managed_specs())

    def _build_intent(self, cleaned, tenant):
        """Translate ``cleaned_data`` + the formset rows into a ``MembershipIntent``."""
        new_identity = None
        if "who" in self.fields and cleaned.get("who") == self.WHO_NEW:
            new_identity = NewIdentitySpec(
                email=cleaned.get("new_user_email") or "",
                first_name=(cleaned.get("new_user_first_name") or "").strip(),
                last_name=(cleaned.get("new_user_last_name") or "").strip(),
            )
        if self.instance.pk:
            # The user is immutable on an edit; the disabled field's cleaned
            # value is the instance's own user either way.
            user = self.instance.user
        else:
            # _clean_who already put a reused account here, so the service's
            # "intent.user wins over new_identity" precedence keeps the
            # get-or-create semantics the who-block promises.
            user = cleaned.get("user")
        plan = self._grant_plan(cleaned)
        return MembershipIntent(
            tenant=tenant,
            is_active=bool(cleaned.get("is_active")),
            user=user,
            new_identity=new_identity,
            own_roles=plan.own,
            managed_grants=plan.managed,
        )

    # ------------------------------------------------------------------ saving
    def save(self, commit=True):
        # ``commit=True`` deliberately never reaches super().save() (the service
        # owns the row, the identity and the transaction), so BaseModelForm's
        # "the data didn't validate" guard has to be re-stated here or the two
        # branches disagree: commit=False would still raise it while commit=True
        # walked into cleaned_data with keys add_error() had removed. Same
        # wording as Django's, because commit=False still raises Django's.
        if self.errors:
            raise ValueError(
                "The %s could not be %s because the data didn't validate."
                % (
                    self.instance._meta.object_name,
                    "created" if self.instance._state.adding else "changed",
                )
            )
        # who=new only creates a user when the email did NOT resolve to an
        # existing account in clean() (instance.user is already populated then).
        creating_new_user = (
            not self.instance.pk
            and "who" in self.fields
            and self.cleaned_data.get("who") == self.WHO_NEW
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
        if commit:
            # The service owns the transaction, the row, the identity, and the
            # grant reconciliation. super().save() is deliberately not called:
            # _post_clean has already built and unique-validated self.instance,
            # and this form has no m2m model fields, so nothing is skipped.
            result = execute_membership_write(
                actor=self._requesting_user,
                intent=self._build_intent(self.cleaned_data, self.cleaned_data["tenant"]),
                membership=self.instance if self.instance.pk else None,
            )
            self.new_user_created = result.identity_created
            self.instance = result.membership
            return result.membership

        instance = super().save(commit=False)
        django_save_m2m = self.save_m2m
        # Captured BEFORE the caller persists the row: once it has, the stored
        # is_active no longer shows a False -> True transition (INV-14). None on
        # create, which fails closed over an empty retained-group set.
        previous_is_active = self._initial_is_active

        def save_m2m():
            # Canonical two-step (instance.save() then form.save_m2m()): chain
            # the grant reconciliation on so the deferred save writes the SAME
            # rows a commit=True save would — not a membership silently stripped
            # of its grants.
            django_save_m2m()
            apply_membership_grants(
                actor=self._requesting_user,
                membership=self.instance,
                plan=self._grant_plan(self.cleaned_data),
                previous_is_active=previous_is_active,
            )

        self.save_m2m = save_m2m
        return instance


class MembershipFilterForm(FilterForm):
    # Class-body position, not a function-body import: breaks a forms <-> filters
    # cycle by running after this module's own definitions.
    from ..filters import MembershipFilterSet

    filterset_class = MembershipFilterSet


class MembershipBulkRoleForm(BulkEditForm):
    """Bulk add/remove direct own-scope grants for selected memberships."""

    roles_to_add = forms.ModelMultipleChoiceField(
        queryset=Role._base_manager.filter(deleted_at__isnull=True),
        required=False,
        label=_("Add roles"),
        widget=forms.SelectMultiple(attrs={"class": "form-select"}),
    )
    roles_to_remove = forms.ModelMultipleChoiceField(
        queryset=Role._base_manager.filter(deleted_at__isnull=True),
        required=False,
        label=_("Remove roles"),
        widget=forms.SelectMultiple(attrs={"class": "form-select"}),
    )
    reason = forms.CharField(
        required=False,
        label=_("Reason for elevated direct grants"),
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    valid_until = forms.DateTimeField(
        required=False,
        label=_("Expiry for elevated direct grants"),
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("add_tags", None)
        self.fields.pop("remove_tags", None)

    def clean(self):
        cleaned = super().clean()
        privileged = any(role_is_privileged(role) for role in cleaned.get("roles_to_add") or [])
        reason = (cleaned.get("reason") or "").strip()
        valid_until = cleaned.get("valid_until")
        cleaned["reason"] = reason
        if privileged:
            if not reason:
                self.add_error("reason", _("Elevated direct grants require a reason."))
            if valid_until is None:
                self.add_error("valid_until", _("Elevated direct grants require an expiration."))
            elif valid_until <= timezone.now():
                self.add_error("valid_until", _("The expiration must be in the future."))
        return cleaned
