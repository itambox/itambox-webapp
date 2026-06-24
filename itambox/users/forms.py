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
        fields = ['username', 'first_name', 'last_name', 'email', 'is_active', 'is_staff', 'is_superuser']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
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

        # Security check: only superusers can modify is_superuser and is_staff
        if not self.request_user or not self.request_user.is_superuser:
            if 'is_superuser' in self.fields:
                self.fields['is_superuser'].disabled = True
            if 'is_staff' in self.fields:
                self.fields['is_staff'].disabled = True

        # Group Manager is the global users.manage_usergroups capability; reflect
        # the current grant and let only superusers change it.
        if self.instance and self.instance.pk:
            self.fields['is_group_manager'].initial = self.instance.user_permissions.filter(
                content_type__app_label='users', codename='manage_usergroups',
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
        """Grant/revoke the global users.manage_usergroups capability. Only a
        superuser may change it (the field is disabled otherwise)."""
        if not self.request_user or not self.request_user.is_superuser:
            return
        from django.contrib.auth.models import Permission
        perm = Permission.objects.filter(
            content_type__app_label='users', codename='manage_usergroups',
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

        # Security check: only superusers can modify is_superuser and is_staff
        if not self.request_user or not self.request_user.is_superuser:
            if 'is_superuser' in self.fields:
                self.fields['is_superuser'].disabled = True
                self.fields['is_superuser'].widget.attrs['disabled'] = 'disabled'
            if 'is_staff' in self.fields:
                self.fields['is_staff'].disabled = True
                self.fields['is_staff'].widget.attrs['disabled'] = 'disabled'

    def clean(self):
        cleaned_data = super().clean()
        selected_fields = self.data.getlist('_selected_fields') if self.data else []

        # Only superusers can change is_superuser / is_staff
        has_privilege_field_selected = 'is_superuser' in selected_fields or 'is_staff' in selected_fields
        if has_privilege_field_selected and (not self.request_user or not self.request_user.is_superuser):
            raise forms.ValidationError(_("Only superusers can grant or modify staff or superuser status."))

        # Prevent non-nullable fields from being set to None if they are selected
        for field_name in ['is_active', 'is_staff', 'is_superuser']:
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
from organization.models import TenantRole
from core.auth.guards import validate_permission_grant
from .models import UserGroup
from .filters import UserGroupFilterSet


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
        queryset=TenantRole._base_manager.none(),
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
    is_active = forms.BooleanField(
        required=False,
        initial=True,
        label=_("Active"),
    )

    class Meta:
        model = UserGroup
        fields = ['name', 'description', 'roles', 'members', 'is_active']

    def __init__(self, *args, user=None, tenant=None, **kwargs):
        # `tenant` is accepted for call-site compatibility but ignored: groups are global.
        self._requesting_user = user
        super().__init__(*args, **kwargs)
        # Roles across ALL tenants (unscoped _base_manager overrides the core/apps.py
        # current-tenant scoping applied during super().__init__); any user as member.
        self.fields['roles'].queryset = TenantRole._base_manager.filter(
            deleted_at__isnull=True,
        ).select_related('tenant').order_by('tenant__name', 'name')
        self.fields['members'].queryset = User.objects.all().order_by('username')

    def clean(self):
        cleaned_data = super().clean()
        # Escalation guard (defence in depth; group management is global-admin only):
        # a non-superuser may attach a role only if they hold every one of its
        # permissions in that role's own tenant. Each role is validated against its own
        # tenant because a group's roles may span tenants.
        for role in (cleaned_data.get('roles') or []):
            validate_permission_grant(self._requesting_user, role.permissions or [], role.tenant)
        return cleaned_data


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

 