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
    is_group_manager = forms.BooleanField(
        required=False,
        label=_("Group Manager"),
        help_text=_("Can create and manage global user groups (which grant cross-tenant access). "
                    "A global capability granted only by superusers."),
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
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

        # Group Manager is the global organization.manage_groups capability; reflect
        # the current grant and let only superusers change it.
        if self.instance and self.instance.pk:
            self.fields['is_group_manager'].initial = self.instance.user_permissions.filter(
                content_type__app_label='organization', codename='manage_groups',
            ).exists()
        if not self.request_user or not self.request_user.is_superuser:
            self.fields['is_group_manager'].disabled = True

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
                Row(
                    Column('is_group_manager', css_class='col-md-12'),
                    css_class='row g-3',
                ),
            ),
        )
        from organization.forms.helpers import add_standard_buttons
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
            self._sync_group_manager(user)
        return user

    def _sync_group_manager(self, user):
        """Grant/revoke the global organization.manage_groups capability. Only a
        superuser may change it (the field is disabled otherwise)."""
        if not self.request_user or not self.request_user.is_superuser:
            return
        from django.contrib.auth.models import Permission
        perm = Permission.objects.filter(
            content_type__app_label='organization', codename='manage_groups',
        ).first()
        if not perm:
            return
        if self.cleaned_data.get('is_group_manager'):
            user.user_permissions.add(perm)
        else:
            user.user_permissions.remove(perm)

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
# UserGroup is an identity-layer construct (relocated here from organization/): it grants
# cross-tenant access, so it lives alongside the User model rather than the business-data
# (organization) layer.
from organization.models import Role, Provider
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
    """Create/edit a global, cross-tenant UserGroup.

    Groups are NOT tenant-bound: ``roles`` may reference roles from any tenant (each
    role label includes its tenant) and ``members`` may be any user. A member gains
    each role's permissions — and access — in that role's tenant. Because managing a
    group can grant cross-tenant access, the views restrict this to global admins; the
    escalation guard here is defence in depth for any non-superuser who reaches the form.
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
        help_text=_("Roles granted to members. A role may belong to any tenant (the "
                    "label shows it); members gain that role's permissions and access "
                    "in that tenant."),
    )
    members = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        required=False,
        label=_("Members"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )
    provider = forms.ModelChoiceField(
        queryset=Provider._base_manager.none(),
        required=False,
        label=_("Provider (SCIM scope)"),
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text=_("Set a provider to make this group SCIM-managed by that provider. "
                    "Leave blank for a global group managed here in the UI."),
    )
    is_active = forms.BooleanField(
        required=False,
        initial=True,
        label=_("Active"),
    )

    class Meta:
        model = UserGroup
        fields = ['name', 'description', 'roles', 'members', 'provider', 'is_active']

    @staticmethod
    def _role_label(role):
        """Prefix each role choice with its container so cross-container roles are legible,
        e.g. ``[Tenant A] Administrator`` vs ``[Provider Northwind] MSP Technician``."""
        if role.scope == Role.SCOPE_PROVIDER and role.provider_id:
            return f"[{role.provider.name}] {role.name}"
        if role.tenant_id:
            return f"[{role.tenant.name}] {role.name}"
        return role.name

    def __init__(self, *args, user=None, tenant=None, **kwargs):
        # `tenant` is accepted for call-site compatibility but ignored: groups are global.
        self._requesting_user = user
        super().__init__(*args, **kwargs)
        # Roles across ALL containers (unscoped _base_manager overrides the core/apps.py
        # current-tenant scoping applied during super().__init__); any user as member.
        self.fields['roles'].queryset = Role._base_manager.filter(
            deleted_at__isnull=True,
        ).select_related('tenant', 'provider').order_by('scope', 'tenant__name', 'provider__name', 'name')
        self.fields['roles'].label_from_instance = self._role_label
        self.fields['members'].queryset = User.objects.all().order_by('username')
        # Scope the SCIM ``provider`` choice to providers the requesting user may manage:
        # setting a group's provider hands that provider's SCIM-synced staff every role the
        # group carries, so a group admin must not be able to point a group at a provider
        # they do not administer (cross-provider takeover). Superuser sees all; blank (a
        # global, UI-managed group) always remains allowed via ``required=False``.
        # Mirrors TechnicianQuickForm.__init__ scoping (organization/forms/provider_form.py).
        provider_qs = Provider._base_manager.filter(deleted_at__isnull=True).order_by('name')
        if self._requesting_user is not None and not self._requesting_user.is_superuser:
            manageable = [
                p.pk for p in provider_qs
                if self._requesting_user.has_perm('organization.manage_groups', obj=p)
            ]
            # Preserve the current value when editing so the field validates: a non-superuser
            # editing a group whose provider they cannot manage still sees it (the change
            # guard in clean() enforces they may not alter it to another unmanaged provider).
            current_provider_id = getattr(self.instance, 'provider_id', None)
            if current_provider_id is not None and current_provider_id not in manageable:
                manageable.append(current_provider_id)
            provider_qs = provider_qs.filter(pk__in=manageable)
        self.fields['provider'].queryset = provider_qs

        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            Fieldset(str(_("Group details")), 'name', 'description', 'is_active'),
            Fieldset(str(_("Access grants")), 'roles', 'members'),
            Fieldset(str(_("Scope")), 'provider'),
        )
        from organization.forms.helpers import add_standard_buttons
        add_standard_buttons(self.helper, self.instance, 'users:usergroup_list')

    def clean(self):
        cleaned_data = super().clean()
        user = self._requesting_user
        is_superuser = bool(user is not None and user.is_superuser)

        # Escalation guard (defence in depth; group management is global-admin only):
        # a non-superuser may attach a role only if they already hold every one of its
        # permissions in that role's OWN container. A group's roles may span containers, so
        # each is validated against ``role.owner`` (the role's tenant OR provider) — using
        # ``role.tenant`` would wrongly reject every provider-scoped role (container=None
        # holds nothing).
        for role in (cleaned_data.get('roles') or []):
            validate_permission_grant(user, role.permissions or [], role.owner)

        # Provider ownership guard (§3-B): setting/changing the SCIM ``provider`` grants
        # that provider's staff every role the group carries. A non-superuser may only
        # pick a provider they manage (``organization.manage_groups`` on it), and when
        # EDITING must be able to manage BOTH the old and the new value — otherwise they
        # could move a group they don't fully control onto a provider they do.
        if not is_superuser and user is not None and 'provider' in cleaned_data:
            new_provider = cleaned_data.get('provider')
            old_provider = self.instance.provider if self.instance.pk else None
            changed = getattr(old_provider, 'pk', None) != getattr(new_provider, 'pk', None)
            # Only guard an actual CHANGE of the SCIM provider. A no-op re-save (editing other
            # fields while leaving the provider untouched) grants nothing new, so it must not be
            # blocked — otherwise a legacy single-company admin who holds manage_groups via a
            # direct user_permissions grant (no Provider membership, so has_perm(obj=provider)
            # is always False) could never edit an existing provider-scoped group at all.
            if changed:
                if new_provider is not None and not user.has_perm(
                    'organization.manage_groups', obj=new_provider,
                ):
                    self.add_error('provider', _(
                        "You do not have permission to manage groups for this provider."
                    ))
                elif old_provider is not None and not user.has_perm(
                    'organization.manage_groups', obj=old_provider,
                ):
                    self.add_error('provider', _(
                        "You do not have permission to move this group away from its "
                        "current provider."
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
    """Used by UserGroupAssignUsersView to pick users to add to a (global) group.

    Groups are global, so any user may be added.
    """
    users = forms.ModelMultipleChoiceField(
        queryset=User.objects.all().order_by('username'),
        required=True,
        label=_("Users"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )

 