# This file is adapted from NetBox (https://github.com/netbox-community/netbox).
# Copyright (c) DigitalOcean, LLC.
# Licensed under the Apache License, Version 2.0.

import logging
from django import forms
from django.contrib.auth import get_user_model
# Import UserPreference from this app's models
from .models import UserPreference 
from django.utils.translation import gettext_lazy as _
from django.conf import settings # Import settings

logger = logging.getLogger(__name__)
User = get_user_model()
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Row, Column, Fieldset, Submit, HTML
from organization.forms.helpers import add_standard_buttons

class UserProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']

class UserForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
        required=False,
        help_text=_("Raw passwords are not stored. If editing a user, leave this blank to keep the current password.")
    )

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'is_active', 'can_login', 'is_staff', 'is_superuser']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_login': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_staff': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_superuser': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.request_user = user
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['password'].required = False
            self.fields['password'].help_text = _("Leave blank to keep the current password.")
        else:
            self.fields['password'].required = True

        # Security check: only superusers can modify is_superuser, is_staff, and can_login
        # (login capability is a global account flag, not a per-tenant setting).
        if not self.request_user or not self.request_user.is_superuser:
            if 'is_superuser' in self.fields:
                self.fields['is_superuser'].disabled = True
            if 'is_staff' in self.fields:
                self.fields['is_staff'].disabled = True
            if 'can_login' in self.fields:
                self.fields['can_login'].disabled = True

        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            Row(
                Column('username', css_class='col-md-8'),
                Column('is_active', css_class='col-md-4'),
                css_class='row g-3',
            ),
            'password',
            Row(
                Column('first_name', css_class='col-md-6'),
                Column('last_name', css_class='col-md-6'),
                css_class='row g-3',
            ),
            'email',
            Fieldset(
                _('Permissions'),
                Row(
                    Column('can_login', css_class='col-md-12'),
                    css_class='row g-3',
                ),
                Row(
                    Column('is_staff', css_class='col-md-6'),
                    Column('is_superuser', css_class='col-md-6'),
                    css_class='row g-3',
                ),
            ),
        )
        add_standard_buttons(self.helper, self.instance, 'users:user_list')

    def clean_is_superuser(self):
        is_superuser = self.cleaned_data.get('is_superuser')
        if is_superuser and (not self.request_user or not self.request_user.is_superuser):
            if self.instance and self.instance.is_superuser:
                return is_superuser
            raise forms.ValidationError(_("Only superusers can grant superuser status."))
        return is_superuser

    def clean_is_staff(self):
        is_staff = self.cleaned_data.get('is_staff')
        if is_staff and (not self.request_user or not self.request_user.is_superuser):
            if self.instance and self.instance.is_staff:
                return is_staff
            raise forms.ValidationError(_("Only superusers can grant staff status."))
        return is_staff

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get('password')
        if password:
            user.set_password(password)
        if commit:
            user.save()
            self.save_m2m()
        return user

class UserPreferencesForm(forms.Form):
    # Define fields explicitly
    pagination_per_page = forms.ChoiceField(
        choices=settings.PAGINATE_COUNT_CHOICES,
        label=_('Items Per Page'),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    theme = forms.ChoiceField(
        choices=UserPreference.THEME_CHOICES, # Reference choices from model
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    language = forms.ChoiceField(
        choices=settings.LANGUAGES,
        required=False,
        label=_('Language'),
        help_text=_('Interface language. Applies across the whole application.'),
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    def __init__(self, user, *args, **kwargs):
        """Load initial data from UserPreference."""
        super().__init__(*args, **kwargs)
        self.user = user
        try:
            # Use filter().first() to avoid DoesNotExist exception
            prefs = UserPreference.objects.filter(user=self.user).first()
            if prefs and prefs.data:
                pagination_prefs = prefs.data.get('pagination', {})
                theme_prefs = prefs.data.get('theme', {})
                
                initial_per_page = pagination_prefs.get('per_page', settings.DEFAULT_PAGINATE_COUNT)
                # Use THEME_LIGHT as the default
                initial_theme = theme_prefs.get('theme', UserPreference.THEME_LIGHT)
                
                # Ensure initial value is valid before setting
                if initial_per_page in dict(settings.PAGINATE_COUNT_CHOICES):
                    self.fields['pagination_per_page'].initial = initial_per_page
                else:
                    # Fallback if stored pref is invalid
                    self.fields['pagination_per_page'].initial = settings.DEFAULT_PAGINATE_COUNT
                
                if initial_theme in dict(UserPreference.THEME_CHOICES):
                     self.fields['theme'].initial = initial_theme
                else:
                     # Fallback if stored pref is invalid
                    self.fields['theme'].initial = UserPreference.THEME_LIGHT
                    
            else:
                # Set defaults if no preferences exist
                self.fields['pagination_per_page'].initial = settings.DEFAULT_PAGINATE_COUNT
                # Use THEME_LIGHT as the default
                self.fields['theme'].initial = UserPreference.THEME_LIGHT
                
        except Exception:
            # Fallback to defaults on any error loading preferences
            self.fields['pagination_per_page'].initial = settings.DEFAULT_PAGINATE_COUNT
             # Use THEME_LIGHT as the default
            self.fields['theme'].initial = UserPreference.THEME_LIGHT

        # Language initial: stored preference first, else the active language
        from django.utils import translation
        valid_languages = dict(settings.LANGUAGES)
        stored_language = None
        try:
            prefs = UserPreference.objects.filter(user=self.user).first()
            if prefs and prefs.data:
                stored_language = prefs.data.get('language')
        except Exception:
            stored_language = None
        if stored_language not in valid_languages:
            stored_language = translation.get_language()
        if stored_language not in valid_languages:
            stored_language = settings.LANGUAGE_CODE
        self.fields['language'].initial = stored_language

    def save(self):
        """Save form data to UserPreference."""
        prefs, created_at = UserPreference.objects.get_or_create(user=self.user)
        
        # Ensure prefs.data is initialized as a dict if it's None or not set
        if prefs.data is None:
            prefs.data = {}
        
        # Update pagination preferences
        if 'pagination' not in prefs.data:
            prefs.data['pagination'] = {}
        prefs.data['pagination']['per_page'] = self.cleaned_data['pagination_per_page']

        # Update theme preferences
        if 'theme' not in prefs.data:
            prefs.data['theme'] = {}
        prefs.data['theme']['theme'] = self.cleaned_data['theme']

        # Update language preference (the active cookie is set by the view)
        language = self.cleaned_data.get('language')
        if language and language in dict(settings.LANGUAGES):
            prefs.data['language'] = language

        prefs.save()

class TableConfigForm(forms.Form):
    """
    Form for configuring table columns.
    Adapted from NetBox pattern.
    """
    available_columns = forms.MultipleChoiceField(
        choices=[],
        required=False,
        widget=forms.SelectMultiple(
            attrs={'size': 10, 'class': 'form-select available-columns'}
        ),
        label=_('Available Columns')
    )
    columns = forms.MultipleChoiceField(
        choices=[],
        required=False,
        widget=forms.SelectMultiple(
            attrs={'size': 10, 'class': 'form-select selected-columns'}
        ),
        label=_('Selected Columns')
    )

    def __init__(self, table, *args, **kwargs):
        self.table = table
        user_config = kwargs.pop('user_config', {}) # e.g., {'columns': [...], 'ordering': [...]} or {}
        super().__init__(*args, **kwargs)

        logger.debug("TableConfigForm received user_config: %s", user_config)

        # Determine initial selected columns (priority: user > table default > all)
        default_cols = getattr(table.Meta, 'default_columns', None)
        initial_selected_names = user_config.get('columns', default_cols)
        # Treat empty list (from Reset) the same as None — fall back to defaults
        if not initial_selected_names:
             # Fallback if no user pref and no Meta.default_columns
             # Use Meta.fields or all non-excluded fields
             if hasattr(table.Meta, 'fields'):
                 initial_selected_names = list(table.Meta.fields)
             else:
                 exclude = getattr(table.Meta, 'exclude', ('pk', 'actions'))
                 initial_selected_names = [name for name in table.base_columns.keys() if name not in exclude]
        
        logger.debug("TableConfigForm initial selected names: %s", initial_selected_names)

        # Populate choices based on the table definition
        all_column_choices = {
            name: str(column.verbose_name) 
            for name, column in table.columns.items()
            if name not in getattr(table.Meta, 'exclude_from_config', ('pk', 'actions')) # Allow explicit exclusion from config
        }
        
        available_choices = []
        selected_choices = []

        # Populate selected_choices based on initial_selected_names order
        for name in initial_selected_names:
            if name in all_column_choices:
                selected_choices.append((name, all_column_choices[name]))
        
        # Populate available_choices with remaining columns, sorted by verbose name
        selected_names_set = set(initial_selected_names)
        available_choices = sorted(
            [
                (name, verbose)
                for name, verbose in all_column_choices.items()
                if name not in selected_names_set
            ],
            key=lambda item: item[1] # Sort by verbose name
        )

        # Assign choices to the form fields
        self.fields['available_columns'].choices = available_choices
        self.fields['columns'].choices = selected_choices
        
        logger.debug("TableConfigForm final available choices: %s", available_choices)
        logger.debug("TableConfigForm final selected choices: %s", selected_choices)

    @property
    def table_name(self):
        if not self.table:
            return None
        app_label = self.table.Meta.model._meta.app_label
        model_name = self.table.Meta.model._meta.model_name
        return f'{app_label}.{model_name}'


class TokenForm(forms.ModelForm):
    expires = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        help_text=_("Optional expiration date. Leave blank for a token that never expires.")
    )
    allowed_ips = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('e.g., 192.168.1.0/24, 10.0.0.5')}),
        help_text=_("Comma-separated IPs or CIDR prefixes allowed to use this token. Leave blank to allow any address."),
    )

    class Meta:
        from .models import Token
        model = Token
        fields = ['description', 'write_enabled', 'allowed_ips', 'expires']
        widgets = {
            'description': forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('e.g., Personal Laptop API Access')}),
            'write_enabled': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # form_tag=False because the surrounding template already provides <form> and the
        # CSRF token; setting it here prevents a nested <form> element being rendered.
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('write_enabled', css_class='col-md-6'),
                Column('expires', css_class='col-md-6'),
                css_class='row g-3',
            ),
            'description',
            'allowed_ips',
        )

    def clean_allowed_ips(self):
        import ipaddress
        raw = self.cleaned_data.get('allowed_ips', '')
        prefixes = [p.strip() for p in raw.replace('\n', ',').split(',') if p.strip()]
        for prefix in prefixes:
            try:
                ipaddress.ip_network(prefix, strict=False)
            except ValueError:
                raise forms.ValidationError(
                    _('"%(prefix)s" is not a valid IP address or CIDR prefix.') % {'prefix': prefix}
                )
        return prefixes


from core.forms import FilterForm, BulkEditForm
from .filters import UserFilterSet


class UserFilterForm(FilterForm):
    filterset_class = UserFilterSet


class UserBulkEditForm(BulkEditForm):
    def __init__(self, *args, request_user=None, **kwargs):
        self.request_user = request_user
        if 'model' not in kwargs:
            kwargs['model'] = get_user_model()
        super().__init__(*args, **kwargs)

        # Remove fields that should not be bulk editable
        forbidden_fields = {'password', 'last_login', 'username', 'first_name', 'last_name', 'email', 'date_joined'}
        for f in forbidden_fields:
            if f in self.fields:
                del self.fields[f]
        if '_selected_fields' in self.fields:
            self.fields['_selected_fields'].choices = [
                c for c in self.fields['_selected_fields'].choices if c[0] not in forbidden_fields
            ]

        self.fields['is_active'] = forms.NullBooleanField(
            required=False,
            label=_('Active'),
            widget=forms.Select(choices=(
                (None, _('— No Change —')),
                (True, _('Yes')),
                (False, _('No')),
            ), attrs={'class': 'form-select'})
        )
        self.fields['is_staff'] = forms.NullBooleanField(
            required=False,
            label=_('Staff'),
            widget=forms.Select(choices=(
                (None, _('— No Change —')),
                (True, _('Yes')),
                (False, _('No')),
            ), attrs={'class': 'form-select'})
        )
        self.fields['is_superuser'] = forms.NullBooleanField(
            required=False,
            label=_('Superuser'),
            widget=forms.Select(choices=(
                (None, _('— No Change —')),
                (True, _('Yes')),
                (False, _('No')),
            ), attrs={'class': 'form-select'})
        )
        self.fields['can_login'] = forms.NullBooleanField(
            required=False,
            label=_('Can log in'),
            widget=forms.Select(choices=(
                (None, _('— No Change —')),
                (True, _('Yes')),
                (False, _('No')),
            ), attrs={'class': 'form-select'})
        )

        # Security check: only superusers can modify is_superuser, is_staff, and can_login
        if not self.request_user or not self.request_user.is_superuser:
            for f in ('is_superuser', 'is_staff', 'can_login'):
                if f in self.fields:
                    self.fields[f].disabled = True
                    self.fields[f].widget.attrs['disabled'] = 'disabled'

    def clean(self):
        cleaned_data = super().clean()
        selected_fields = self.data.getlist('_selected_fields') if self.data else []

        # Only superusers can change is_superuser / is_staff / can_login
        has_privilege_field_selected = any(
            f in selected_fields for f in ('is_superuser', 'is_staff', 'can_login')
        )
        if has_privilege_field_selected and (not self.request_user or not self.request_user.is_superuser):
            raise forms.ValidationError(_("Only superusers can grant or modify staff, superuser, or login status."))

        # Prevent non-nullable fields from being set to None if they are selected
        for field_name in ['is_active', 'is_staff', 'is_superuser', 'can_login']:
            if field_name in selected_fields:
                val = cleaned_data.get(field_name)
                # If they are superuser, these fields aren't disabled and we validate they aren't None.
                # If they aren't superuser, they were already validated/gated above, so we only need to validate fields that aren't disabled (i.e. is_active).
                if val is None and (self.request_user and self.request_user.is_superuser or field_name == 'is_active'):
                    self.add_error(field_name, _("Please select Yes or No for this field."))

        return cleaned_data


# --------------------------------------------------------------------------- UserGroup
# UserGroup is an identity-layer construct (relocated here from organization/):
# provider-owned groups may be projected into managed tenants through RoleGrant scopes.
from organization.models import Membership, Role, Tenant
from organization.access import accessible_tenant_ids
from core.auth.guards import validate_permission_grant, validate_group_membership_grant
from .models import UserGroup
from .filters import UserGroupFilterSet


class _RolesHolder:
    """Lightweight stand-in exposing ``roles.all()`` for an unsaved role set.

    ``validate_group_membership_grant`` iterates ``group.roles.all()``; on a create (no pk)
    the submitted roles have no persisted M2M yet, so we wrap the in-memory list to satisfy
    that contract without saving prematurely.
    """
    class _Manager:
        def __init__(self, roles):
            self._roles = roles

        def all(self):
            return self._roles

    def __init__(self, roles):
        self.roles = self._Manager(roles)


class UserGroupForm(forms.ModelForm):
    """Compatibility writer for a flat, tenant-owned phase-5 UserGroup.

    It still writes the legacy M2Ms during comparison mode, whose signals create
    GroupMembership/RoleGrant shadows. New invalid shapes are rejected: the
    owner is mandatory, roles must be owner-owned, and every selected user must
    have an active Membership in that owner.
    """
    name = forms.CharField(
        max_length=100,
        label=_("Name"),
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    description = forms.CharField(
        required=False,
        label=_("Description"),
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
    )
    roles = forms.ModelMultipleChoiceField(
        queryset=Role._base_manager.none(),
        required=False,
        label=_("Roles"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
        help_text=_("Owner-owned roles applied inside the group's own tenant. Managed "
                    "projections are configured on RoleGrant scopes."),
    )
    members = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        required=False,
        label=_("Members"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )
    tenant = forms.ModelChoiceField(
        queryset=Tenant._base_manager.none(),
        required=True,
        label=_("Owning tenant"),
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text=_("The tenant/provider that owns this group and its member identities."),
    )
    is_active = forms.BooleanField(
        required=False,
        initial=True,
        label=_("Active"),
    )

    class Meta:
        model = UserGroup
        fields = ['name', 'description', 'roles', 'members', 'tenant', 'is_active']

    @staticmethod
    def _role_label(role):
        """Prefix each role choice with its owner while the legacy picker remains."""
        return f"[{role.tenant.name}] {role.name}"

    def __init__(self, *args, user=None, tenant=None, **kwargs):
        # ``tenant`` remains accepted for call-site compatibility; authorization
        # is derived from the explicit owner submitted on the group.
        self._requesting_user = user
        super().__init__(*args, **kwargs)
        is_superuser = bool(self._requesting_user is not None and self._requesting_user.is_superuser)
        # The compatibility form displays unscoped candidates, then clean()
        # requires owner-owned roles and active owner Memberships.
        self.fields['roles'].queryset = Role._base_manager.filter(
            deleted_at__isnull=True,
        ).select_related('tenant').order_by('tenant__name', 'name')
        self.fields['roles'].label_from_instance = self._role_label
        self.fields['members'].queryset = User.objects.all().order_by('username')
        # Scope the owning-``tenant`` choice to tenants where the requesting user holds
        # ``users.change_usergroup``: a group's tenant decides whose admins and whose SCIM
        # token control it, so a group admin must not be able to hand a group to (or
        # create one under) a tenant they do not administer. Superuser sees all tenants
        # plus blank (= global group).
        tenant_qs = Tenant._base_manager.filter(deleted_at__isnull=True).order_by('name')
        if self._requesting_user is not None and not is_superuser:
            manageable = [
                t.pk for t in Tenant._base_manager.filter(
                    pk__in=accessible_tenant_ids(self._requesting_user),
                    deleted_at__isnull=True,
                )
                if self._requesting_user.has_perm('users.change_usergroup', obj=t)
            ]
            # Preserve the current value when editing so the field validates: a non-superuser
            # editing a group whose tenant they cannot manage still sees it (the change
            # guard in clean() enforces they may not alter it).
            current_tenant_id = getattr(self.instance, 'tenant_id', None)
            if current_tenant_id is not None and current_tenant_id not in manageable:
                manageable.append(current_tenant_id)
            tenant_qs = tenant_qs.filter(pk__in=manageable)
        self.fields['tenant'].queryset = tenant_qs
        # Blank tenant = global group, superuser-only. The core/apps.py BaseForm patch
        # force-sets tenant.required=True during super().__init__; re-decide it here:
        # superusers (and guard-exempt programmatic use without a user) may leave it
        # blank, non-superusers must pick a tenant — except when re-saving an existing
        # global group, where keeping blank is a no-op (changes are guarded in clean()).
        self.fields['tenant'].required = True

        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            Fieldset(str(_("Group details")), 'name', 'description', 'is_active'),
            Fieldset(str(_("Access grants")), 'roles', 'members'),
            Fieldset(str(_("Scope")), 'tenant'),
        )
        add_standard_buttons(self.helper, self.instance, 'users:usergroup_list')

    def clean(self):
        cleaned_data = super().clean()
        user = self._requesting_user
        is_superuser = bool(user is not None and user.is_superuser)
        owner = cleaned_data.get('tenant')

        if owner is None:
            self.add_error('tenant', _('Every user group must have an owning tenant.'))
        else:
            foreign_roles = [
                role for role in (cleaned_data.get('roles') or [])
                if role.tenant_id != owner.pk
            ]
            if foreign_roles:
                self.add_error(
                    'roles',
                    _('A group may carry only roles owned by its owning tenant.'),
                )
            member_ids = [member.pk for member in (cleaned_data.get('members') or [])]
            active_member_ids = set(Membership.objects.filter(
                tenant=owner,
                user_id__in=member_ids,
                is_active=True,
            ).values_list('user_id', flat=True))
            if set(member_ids) - active_member_ids:
                self.add_error(
                    'members',
                    _('Every group member must have an active Membership in the owning tenant.'),
                )

        # Escalation guard (defence in depth; group management is admin-gated in the
        # views): a non-superuser may attach a role only if they already hold every one
        # of its permissions in the group's owning tenant.
        for role in (cleaned_data.get('roles') or []):
            validate_permission_grant(user, role.permissions or [], role.owner)

        # Ownership guard: setting/changing the owning ``tenant`` re-keys which tenant's
        # admins and SCIM token control the group. A non-superuser may only pick a tenant
        # where they hold ``users.change_usergroup``, and when EDITING must hold it for
        # BOTH the old and the new value — otherwise they could move a group they don't
        # fully control onto a tenant they do. A blank (global) side is superuser-only.
        # A no-op re-save (tenant untouched) grants nothing new and is not blocked.
        if not is_superuser and user is not None and 'tenant' in cleaned_data:
            new_tenant = cleaned_data.get('tenant')
            old_tenant = self.instance.tenant if self.instance.pk else None
            if not self.instance.pk:
                if new_tenant is None:
                    self.add_error('tenant', _(
                        "Only superusers can create global user groups."
                    ))
                elif not user.has_perm('users.change_usergroup', obj=new_tenant):
                    self.add_error('tenant', _(
                        "You do not have permission to manage user groups for this tenant."
                    ))
            elif getattr(old_tenant, 'pk', None) != getattr(new_tenant, 'pk', None):
                if new_tenant is None or not user.has_perm(
                    'users.change_usergroup', obj=new_tenant,
                ):
                    self.add_error('tenant', _(
                        "You do not have permission to manage user groups for this tenant."
                    ))
                elif old_tenant is None or not user.has_perm(
                    'users.change_usergroup', obj=old_tenant,
                ):
                    self.add_error('tenant', _(
                        "You do not have permission to move this group away from its "
                        "current owning tenant."
                    ))

        # Member-grant escalation guard (§3-C): adding a member confers every role the
        # group carries. If the group carries roles (existing or being set on this save)
        # and members are being set/added, validate each carried role against the actor's
        # held permissions — the same check enforced in UserGroupAssignUsersView, so the
        # form write path is covered too. Reuse the shared helper for parity.
        if not is_superuser and user is not None and cleaned_data.get('members'):
            group_for_check = self._group_for_membership_check(cleaned_data)
            if group_for_check is not None:
                try:
                    validate_group_membership_grant(user, group_for_check)
                except forms.ValidationError as exc:
                    self.add_error('members', exc)

        return cleaned_data

    def _group_for_membership_check(self, cleaned_data):
        """Return an object exposing a ``roles.all()`` accessor reflecting the roles the
        group will carry after this save, for ``validate_group_membership_grant``.

        On create the group has no pk yet, so ``self.instance.roles`` is empty — use the
        submitted ``roles`` instead. On edit, the submitted ``roles`` supersede the stored
        set when the field is present; otherwise fall back to the persisted roles.
        """
        if 'roles' in cleaned_data:
            roles = list(cleaned_data.get('roles') or [])
            return _RolesHolder(roles)
        if self.instance.pk:
            return self.instance
        return None


class UserGroupFilterForm(FilterForm):
    filterset_class = UserGroupFilterSet


class UserGroupAssignUsersForm(forms.Form):
    """Pick active members of the group's owning tenant."""
    users = forms.ModelMultipleChoiceField(
        queryset=User.objects.all().order_by('username'),
        required=True,
        label=_("Users"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )

    def __init__(self, *args, group, **kwargs):
        super().__init__(*args, **kwargs)
        if group.tenant_id is None:
            self.fields['users'].queryset = User.objects.none()
            return
        self.fields['users'].queryset = User.objects.filter(
            memberships__tenant_id=group.tenant_id,
            memberships__is_active=True,
        ).distinct().order_by('username')
