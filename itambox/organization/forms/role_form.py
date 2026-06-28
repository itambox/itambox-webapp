"""Unified Role form (tenant- and provider-scoped) with permission-matrix UI.

A single ``RoleForm`` handles every kind of role:

  * tenant-scoped roles (``scope='tenant'``) — the per-tenant role the prior ``TenantRole``
    used to carry. Renders the per-model CRUD matrix only.
  * provider-scoped roles (``scope='provider'``) — the MSP staff role projected across the
    provider's tenants (replaces ``ProviderRoleTemplate`` + ``ProviderRole``). Renders the
    per-model CRUD matrix **and** the provider capability section
    (``organization.manage_tenants`` / ``manage_staff`` / ``manage_groups`` / ``manage_provider``).
"""
from django import forms
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout
from core.forms import FilterForm
from core.auth.guards import validate_permission_grant
from ..models import Role, Tenant, Provider


MATRIX_MODELS = {
    # Inventory & Hardware
    'asset': {'label': _('Assets'), 'app': 'assets', 'model_name': 'asset', 'group': _('Inventory & Hardware')},
    'assetrequest': {'label': _('Asset Requests'), 'app': 'assets', 'model_name': 'assetrequest', 'group': _('Inventory & Hardware')},
    'purchaseorder': {'label': _('Purchase Orders'), 'app': 'procurement', 'model_name': 'purchaseorder', 'group': _('Inventory & Hardware')},
    'auditsession': {'label': _('Audit Sessions'), 'app': 'compliance', 'model_name': 'auditsession', 'group': _('Compliance & Custody')},
    'assetaudit': {'label': _('Asset Audits'), 'app': 'compliance', 'model_name': 'assetaudit', 'group': _('Compliance & Custody')},
    'accessory': {'label': _('Accessories'), 'app': 'inventory', 'model_name': 'accessory', 'group': _('Inventory & Hardware')},
    'consumable': {'label': _('Consumables'), 'app': 'inventory', 'model_name': 'consumable', 'group': _('Inventory & Hardware')},
    'kit': {'label': _('Kits'), 'app': 'inventory', 'model_name': 'kit', 'group': _('Inventory & Hardware')},
    'component': {'label': _('Components'), 'app': 'inventory', 'model_name': 'component', 'group': _('Inventory & Hardware')},

    # Software & Subscriptions
    'license': {'label': _('Licenses'), 'app': 'licenses', 'model_name': 'license', 'group': _('Software & Subscriptions')},
    'software': {'label': _('Software'), 'app': 'software', 'model_name': 'software', 'group': _('Software & Subscriptions')},
    'subscription': {'label': _('Subscriptions'), 'app': 'subscriptions', 'model_name': 'subscription', 'group': _('Software & Subscriptions')},
    'subscriptionassignment': {'label': _('Subscription Assignments'), 'app': 'subscriptions', 'model_name': 'subscriptionassignment', 'group': _('Software & Subscriptions')},

    # Organization & Structure
    'location': {'label': _('Locations'), 'app': 'organization', 'model_name': 'location', 'group': _('Organization & Structure')},
    'site': {'label': _('Sites'), 'app': 'organization', 'model_name': 'site', 'group': _('Organization & Structure')},
    'assetholder': {'label': _('Asset Holders'), 'app': 'organization', 'model_name': 'assetholder', 'group': _('Organization & Structure')},
    'role': {'label': _('Roles & Permissions'), 'app': 'organization', 'model_name': 'role', 'group': _('Organization & Structure')},
    'membership': {'label': _('Memberships'), 'app': 'organization', 'model_name': 'membership', 'group': _('Organization & Structure')},
    'region': {'label': _('Regions'), 'app': 'organization', 'model_name': 'region', 'group': _('Organization & Structure')},
    'sitegroup': {'label': _('Site Groups'), 'app': 'organization', 'model_name': 'sitegroup', 'group': _('Organization & Structure')},
    'tenantgroup': {'label': _('Tenant Groups'), 'app': 'organization', 'model_name': 'tenantgroup', 'group': _('Organization & Structure')},
    'contact': {'label': _('Contacts'), 'app': 'organization', 'model_name': 'contact', 'group': _('Organization & Structure')},
    'contactrole': {'label': _('Contact Roles'), 'app': 'organization', 'model_name': 'contactrole', 'group': _('Organization & Structure')},
    'tenantinvitation': {'label': _('Tenant Invitations'), 'app': 'organization', 'model_name': 'tenantinvitation', 'group': _('Organization & Structure')},

    # Metadata & Settings
    'manufacturer': {'label': _('Manufacturers'), 'app': 'assets', 'model_name': 'manufacturer', 'group': _('Metadata & Settings')},
    'supplier': {'label': _('Suppliers (Hardware)'), 'app': 'assets', 'model_name': 'supplier', 'group': _('Metadata & Settings')},
    'provider_sub': {'label': _('Providers (Subscription)'), 'app': 'subscriptions', 'model_name': 'provider', 'group': _('Metadata & Settings')},
    'statuslabel': {'label': _('Status Labels'), 'app': 'assets', 'model_name': 'statuslabel', 'group': _('Metadata & Settings')},
    'category': {'label': _('Categories'), 'app': 'assets', 'model_name': 'category', 'group': _('Metadata & Settings')},
    'depreciation': {'label': _('Depreciation Schedules'), 'app': 'assets', 'model_name': 'depreciation', 'group': _('Metadata & Settings')},
    'assettype': {'label': _('Asset Types'), 'app': 'assets', 'model_name': 'assettype', 'group': _('Metadata & Settings')},
    'customfield': {'label': _('Custom Fields'), 'app': 'extras', 'model_name': 'customfield', 'group': _('Metadata & Settings')},
    'tag': {'label': _('Tags'), 'app': 'extras', 'model_name': 'tag', 'group': _('Metadata & Settings')},

    # System & Reporting
    'reporttemplate': {'label': _('Report Templates'), 'app': 'extras', 'model_name': 'reporttemplate', 'group': _('System & Reporting')},
    'scheduledreport': {'label': _('Scheduled Reports'), 'app': 'extras', 'model_name': 'scheduledreport', 'group': _('System & Reporting')},
    'alertrule': {'label': _('Alert Rules'), 'app': 'extras', 'model_name': 'alertrule', 'group': _('System & Reporting')},
    'alertlog': {'label': _('Alert Logs'), 'app': 'extras', 'model_name': 'alertlog', 'group': _('System & Reporting')},
    'notificationchannel': {'label': _('Notification Channels'), 'app': 'extras', 'model_name': 'notificationchannel', 'group': _('System & Reporting')},
    'exporttemplate': {'label': _('Export Templates'), 'app': 'extras', 'model_name': 'exporttemplate', 'group': _('System & Reporting')},
    'webhookendpoint': {'label': _('Webhook Endpoints'), 'app': 'extras', 'model_name': 'webhookendpoint', 'group': _('System & Reporting')},
    'eventrule': {'label': _('Event Rules'), 'app': 'extras', 'model_name': 'eventrule', 'group': _('System & Reporting')},
    'labeltemplate': {'label': _('Label Templates'), 'app': 'extras', 'model_name': 'labeltemplate', 'group': _('System & Reporting')},
    'recyclebin': {'label': _('Recycle Bin'), 'app': 'core', 'model_name': 'recyclebin', 'group': _('System & Reporting')},

    # Compliance & Custody
    'custodytemplate': {'label': _('Custody Templates'), 'app': 'compliance', 'model_name': 'custodytemplate', 'group': _('Compliance & Custody')},
    'custodyreceipt': {'label': _('Custody Receipts'), 'app': 'compliance', 'model_name': 'custodyreceipt', 'group': _('Compliance & Custody')},
    'assetmaintenance': {'label': _('Asset Maintenances'), 'app': 'assets', 'model_name': 'assetmaintenance', 'group': _('Inventory & Hardware')},

    'user': {'label': _('Users'), 'app': 'users', 'model_name': 'user', 'group': _('User Management')},
    'token': {'label': _('API Tokens'), 'app': 'users', 'model_name': 'token', 'group': _('User Management')},
    'usergroup': {'label': _('User Groups'), 'app': 'users', 'model_name': 'usergroup', 'group': _('User Management')},

    'warranty': {'label': _('Warranties'), 'app': 'assets', 'model_name': 'warranty', 'group': _('Inventory & Hardware')},
    'assetdisposal': {'label': _('Asset Disposals'), 'app': 'assets', 'model_name': 'assetdisposal', 'group': _('Inventory & Hardware')},
    'assetreservation': {'label': _('Asset Reservations'), 'app': 'assets', 'model_name': 'assetreservation', 'group': _('Inventory & Hardware')},
    'contract': {'label': _('Contracts'), 'app': 'procurement', 'model_name': 'contract', 'group': _('Inventory & Hardware')},
    'installedsoftware': {'label': _('Installed Software'), 'app': 'software', 'model_name': 'installedsoftware', 'group': _('Software & Subscriptions')},
    'assetrole': {'label': _('Asset Roles'), 'app': 'assets', 'model_name': 'assetrole', 'group': _('Metadata & Settings')},
    'costcenter': {'label': _('Cost Centers'), 'app': 'organization', 'model_name': 'costcenter', 'group': _('Organization & Structure')},
    'journalentry': {'label': _('Journal Entries'), 'app': 'extras', 'model_name': 'journalentry', 'group': _('System & Reporting')},
    'configcontext': {'label': _('Config Contexts'), 'app': 'extras', 'model_name': 'configcontext', 'group': _('System & Reporting')},

    # Plugins
    'docusignenvelope': {'label': _('DocuSign Envelopes'), 'app': 'itambox_esign', 'model_name': 'docusignenvelope', 'group': _('Plugins')},
}

# Drop plugin entries when the plugin is not installed; their permission codenames don't
# exist and the whitelist validation would reject them otherwise.
from django.apps import apps as _django_apps  # noqa: E402
for _plugin_key, _plugin_app in (('docusignenvelope', 'itambox_esign'),):
    if not _django_apps.is_installed(_plugin_app):
        MATRIX_MODELS.pop(_plugin_key, None)


# Provider-level capabilities only meaningful on provider-scoped roles. Registered as
# permissions on ``organization.Provider`` (see ``Provider.Meta.permissions``).
PROVIDER_CAPABILITIES = [
    ('manage_provider', _('Manage provider settings')),
    ('manage_tenants',  _('Manage customer tenants')),
    ('manage_staff',    _('Manage provider staff')),
    ('manage_groups',   _('Manage user groups')),
]

# Custom (non-CRUD) permissions exposed as named checkboxes alongside the matrix.
CUSTOM_PERMISSIONS = [
    ('add_delegated_assetrequest', _('Submit asset requests on behalf of others'), 'assets.add_delegated_assetrequest'),
    ('receive_purchaseorder',      _('Receive purchase orders'), 'procurement.receive_purchaseorder'),
    ('approve_purchaseorder',      _('Approve purchase orders'), 'procurement.approve_purchaseorder'),
]


class RoleForm(forms.ModelForm):
    """Unified ModelForm for ``organization.Role`` (tenant- or provider-scoped)."""

    tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.none(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Tenant"),
    )
    provider = forms.ModelChoiceField(
        queryset=Provider.objects.none(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Provider"),
    )
    scope = forms.ChoiceField(
        choices=Role.SCOPE_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Scope"),
    )

    class Meta:
        model = Role
        fields = ['name', 'scope', 'tenant', 'provider', 'description', 'is_default']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Inventory Manager'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': _('Describe the role…')}),
            'is_default': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        self.current_user = kwargs.pop('user', None)
        self.tenant = kwargs.pop('tenant', None)
        self.provider = kwargs.pop('provider', None)
        super().__init__(*args, **kwargs)

        # Scope is decided by which container the form is bound to (tenant vs provider),
        # not picked by the user. We populate ``initial`` and mark the field disabled so
        # Django always derives the value from initial, regardless of what the data dict
        # carries — this keeps the form valid for both short ({"name":…}) and full submits.
        if self.instance.pk:
            self.fields['scope'].initial = self.instance.scope
        else:
            self.fields['scope'].initial = self.initial.get('scope') or (
                Role.SCOPE_PROVIDER if self.provider else Role.SCOPE_TENANT
            )
        self.fields['scope'].required = False
        self.fields['scope'].disabled = True

        # Tenant / Provider field configuration. Both fields stay ``required=False`` at
        # the field level — container presence is enforced in ``clean()`` against the
        # resolved scope so we can carry the value through hidden inputs or context kwargs
        # without tripping the auto-required validation when a form is re-submitted.
        self.fields['tenant'].required = False
        self.fields['provider'].required = False
        if self.instance.pk and self.instance.scope == Role.SCOPE_TENANT:
            self.fields['tenant'].queryset = Tenant.objects.filter(pk=self.instance.tenant_id)
            self.fields['tenant'].initial = self.instance.tenant_id
            self.fields['tenant'].widget = forms.HiddenInput()
            self.fields['tenant'].disabled = True
            self.fields['provider'].queryset = Provider.objects.none()
            self.fields['provider'].widget = forms.HiddenInput()
            self.fields['provider'].disabled = True
        elif self.instance.pk and self.instance.scope == Role.SCOPE_PROVIDER:
            self.fields['provider'].queryset = Provider.objects.filter(pk=self.instance.provider_id)
            self.fields['provider'].initial = self.instance.provider_id
            self.fields['provider'].widget = forms.HiddenInput()
            self.fields['provider'].disabled = True
            self.fields['tenant'].queryset = Tenant.objects.none()
            self.fields['tenant'].widget = forms.HiddenInput()
            self.fields['tenant'].disabled = True
        else:
            # Creating
            self.fields['tenant'].queryset = Tenant.objects.all()
            self.fields['provider'].queryset = Provider.objects.all()
            if self.tenant is not None:
                self.fields['tenant'].initial = self.tenant.pk
                self.fields['tenant'].widget = forms.HiddenInput()
                self.fields['tenant'].disabled = True
            elif self.provider is None:
                # No container context — the user picks one, and which one is required
                # follows the scope they pick. We default scope to 'tenant', so the
                # tenant field starts out as the visible, required picker.
                self.fields['tenant'].required = True
            if self.provider is not None:
                self.fields['provider'].initial = self.provider.pk
                self.fields['provider'].widget = forms.HiddenInput()
                self.fields['provider'].disabled = True

        # Build the CRUD matrix and pre-check from the instance's permission set.
        existing_perms = set(self.instance.permissions or [])
        for key, info in MATRIX_MODELS.items():
            for action in ('read', 'create', 'edit', 'delete'):
                fname = f'perm_{key}_{action}'
                self.fields[fname] = forms.BooleanField(
                    required=False,
                    widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
                )
            app, model = info['app'], info['model_name']
            self.fields[f'perm_{key}_read'].initial = f'{app}.view_{model}' in existing_perms
            self.fields[f'perm_{key}_create'].initial = f'{app}.add_{model}' in existing_perms
            self.fields[f'perm_{key}_edit'].initial = f'{app}.change_{model}' in existing_perms
            self.fields[f'perm_{key}_delete'].initial = f'{app}.delete_{model}' in existing_perms

        # Custom (non-CRUD) permissions.
        for codename, label, full in CUSTOM_PERMISSIONS:
            fname = f'perm_{codename}'
            self.fields[fname] = forms.BooleanField(
                required=False, label=label,
                widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            )
            self.fields[fname].initial = full in existing_perms

        # Provider capabilities (rendered only on provider scope; cleaning ignores them
        # for tenant-scoped roles).
        for codename, label in PROVIDER_CAPABILITIES:
            fname = f'cap_{codename}'
            self.fields[fname] = forms.BooleanField(
                required=False, label=label,
                widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            )
            self.fields[fname].initial = f'organization.{codename}' in existing_perms

        # Crispy layout — the matrix sections render through {{ form.matrix_grouped_items }},
        # so we only need to lay out the meta fields here.
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        layout_fields = ['name', 'scope']
        if self.tenant is None and self.provider is None and not self.instance.pk:
            layout_fields.extend(['tenant', 'provider'])
        layout_fields.extend(['description', 'is_default'])
        self.helper.layout = Layout(*layout_fields)
        from .helpers import add_standard_buttons
        add_standard_buttons(self.helper, self.instance, 'organization:role_list')

    # ------------------------------------------------------------------ cleaning
    def clean(self):
        cleaned_data = super().clean()

        # Resolve scope (editing-locked or from cleaned data).
        scope = self.instance.scope if self.instance.pk else cleaned_data.get('scope') or Role.SCOPE_TENANT
        cleaned_data['scope'] = scope

        # Build permission set from matrix + custom + provider capability checkboxes.
        assigned_perms = set()
        for key, info in MATRIX_MODELS.items():
            app, model = info['app'], info['model_name']
            if cleaned_data.get(f'perm_{key}_read'):
                assigned_perms.add(f'{app}.view_{model}')
            if cleaned_data.get(f'perm_{key}_create'):
                assigned_perms.add(f'{app}.add_{model}')
            if cleaned_data.get(f'perm_{key}_edit'):
                assigned_perms.add(f'{app}.change_{model}')
            if cleaned_data.get(f'perm_{key}_delete'):
                assigned_perms.add(f'{app}.delete_{model}')

        for codename, _label, full in CUSTOM_PERMISSIONS:
            if cleaned_data.get(f'perm_{codename}'):
                assigned_perms.add(full)

        if scope == Role.SCOPE_PROVIDER:
            for codename, _label in PROVIDER_CAPABILITIES:
                if cleaned_data.get(f'cap_{codename}'):
                    assigned_perms.add(f'organization.{codename}')

        # If any permission is granted, also auto-grant dashboard perms so the user has a
        # functioning landing page.
        if assigned_perms:
            assigned_perms |= {
                'extras.view_dashboard', 'extras.change_dashboard',
                'extras.add_dashboard',  'extras.delete_dashboard',
            }

        # Filter against the live permission table to drop codenames that don't exist
        # (matrix rows for models lacking that action, uninstalled plugins, etc.).
        from django.contrib.auth.models import Permission
        valid = set(
            f'{p.content_type.app_label}.{p.codename}'
            for p in Permission.objects.select_related('content_type').all()
        )
        assigned_perms = {p for p in assigned_perms if p in valid}

        # Resolve owning Tenant/Provider for the role.
        tenant = cleaned_data.get('tenant') or self.tenant
        provider = cleaned_data.get('provider') or self.provider
        if scope == Role.SCOPE_TENANT:
            if tenant is None and self.instance.tenant_id:
                tenant = self.instance.tenant
            if tenant is None:
                raise forms.ValidationError(_("A tenant is required for tenant-scoped roles."))
            self.instance.tenant = tenant
            self.instance.provider = None
            cleaned_data['tenant'] = tenant
            cleaned_data['provider'] = None
            container = tenant
        else:  # SCOPE_PROVIDER
            if provider is None and self.instance.provider_id:
                provider = self.instance.provider
            if provider is None:
                raise forms.ValidationError(_("A provider is required for provider-scoped roles."))
            self.instance.provider = provider
            self.instance.tenant = None
            cleaned_data['provider'] = provider
            cleaned_data['tenant'] = None
            container = provider
        self.instance.scope = scope

        # Privilege-escalation guard: a non-superuser may not assign permissions they do
        # not themselves hold in the role's container (tenant OR provider).
        validate_permission_grant(self.current_user, assigned_perms, container)

        self.instance.permissions = sorted(assigned_perms)
        return cleaned_data

    # ---------------------------------------------------------------- template helpers
    @property
    def matrix_items(self):
        return [
            {
                'key': key,
                'label': info['label'],
                'read_field': self[f'perm_{key}_read'],
                'create_field': self[f'perm_{key}_create'],
                'edit_field': self[f'perm_{key}_edit'],
                'delete_field': self[f'perm_{key}_delete'],
            }
            for key, info in MATRIX_MODELS.items()
        ]

    @property
    def matrix_grouped_items(self):
        groups = {}
        for key, info in MATRIX_MODELS.items():
            groups.setdefault(info.get('group', 'Other'), []).append({
                'key': key,
                'label': info['label'],
                'read_field': self[f'perm_{key}_read'],
                'create_field': self[f'perm_{key}_create'],
                'edit_field': self[f'perm_{key}_edit'],
                'delete_field': self[f'perm_{key}_delete'],
            })
        return groups

    @property
    def custom_permission_fields(self):
        return [(label, self[f'perm_{codename}']) for codename, label, _ in CUSTOM_PERMISSIONS]

    @property
    def provider_capability_fields(self):
        return [(label, self[f'cap_{codename}']) for codename, label in PROVIDER_CAPABILITIES]


class RoleFilterForm(FilterForm):
    from ..filters import RoleFilterSet  # local to avoid cycle at import time
    filterset_class = RoleFilterSet


class RoleAssignUsersForm(forms.Form):
    """Bulk-add users to a Role (used by the "Assign Users" action)."""
    from django.contrib.auth import get_user_model as _gum
    users = forms.ModelMultipleChoiceField(
        queryset=_gum().objects.all(),
        required=True,
        label=_("Users"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )
