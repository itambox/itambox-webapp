"""Role form (tenant-owned permission set) with permission-matrix UI.

Post-collapse there is exactly one kind of role: a permission set owned by a
tenant. The owner is never picked on the form — it comes from context (the
``?tenant=`` deep-link or the active tenant) on create and is immutable on edit.
Roles owned by a managing (``is_provider``) tenant can additionally be shared
with its managed tenants via the ``shared_with_managed`` checkbox.
"""
from django import forms
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout
from core.forms import FilterForm
from core.auth.guards import validate_permission_grant
from core.managers import get_current_tenant
from ..models import Role
from .helpers import add_standard_buttons


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
    'roleassignment': {'label': _('Role Assignments'), 'app': 'organization', 'model_name': 'roleassignment', 'group': _('Organization & Structure')},
    'region': {'label': _('Regions'), 'app': 'organization', 'model_name': 'region', 'group': _('Organization & Structure')},
    'sitegroup': {'label': _('Site Groups'), 'app': 'organization', 'model_name': 'sitegroup', 'group': _('Organization & Structure')},
    'tenantgroup': {'label': _('Tenant Groups'), 'app': 'organization', 'model_name': 'tenantgroup', 'group': _('Organization & Structure')},
    'contact': {'label': _('Contacts'), 'app': 'organization', 'model_name': 'contact', 'group': _('Organization & Structure')},
    'contactrole': {'label': _('Contact Roles'), 'app': 'organization', 'model_name': 'contactrole', 'group': _('Organization & Structure')},

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
for _plugin_key, _plugin_app in (('docusignenvelope', 'itambox_esign'),):
    if not django_apps.is_installed(_plugin_app):
        MATRIX_MODELS.pop(_plugin_key, None)


# Custom (non-CRUD) permissions exposed as named checkboxes alongside the matrix.
CUSTOM_PERMISSIONS = [
    ('add_delegated_assetrequest', _('Submit asset requests on behalf of others'), 'assets.add_delegated_assetrequest'),
    ('receive_purchaseorder',      _('Receive purchase orders'), 'procurement.receive_purchaseorder'),
    ('approve_purchaseorder',      _('Approve purchase orders'), 'procurement.approve_purchaseorder'),
]


class RoleForm(forms.ModelForm):
    """ModelForm for ``organization.Role`` — owner tenant comes from context, never a picker."""

    class Meta:
        model = Role
        fields = ['name', 'description', 'shared_with_managed']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Inventory Manager'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': _('Describe the role…')}),
            # role="switch" pairs with the .form-switch wrapper role_form.html renders it
            # in — a highlighted switch, not a plain checkbox (RBAC_STAGE3_SPEC.md §4).
            'shared_with_managed': forms.CheckboxInput(attrs={'class': 'form-check-input', 'role': 'switch'}),
        }

    def __init__(self, *args, **kwargs):
        self.current_user = kwargs.pop('user', None)
        tenant_ctx = kwargs.pop('tenant', None)
        super().__init__(*args, **kwargs)

        # Owner resolution: locked to the instance's tenant on edit; on create it is the
        # context tenant (?tenant= deep-link from the view) falling back to the active
        # tenant. There is deliberately no owner picker — a role always lives in the
        # tenant you are working in.
        if self.instance.pk:
            self.owner_tenant = self.instance.tenant
        else:
            self.owner_tenant = tenant_ctx or get_current_tenant()

        # Sharing is only meaningful when the owner manages other tenants — the
        # switch never renders for a plain tenant's role, even for a superuser.
        if not (self.owner_tenant is not None and self.owner_tenant.is_provider):
            self.fields.pop('shared_with_managed', None)
        else:
            # Single source of truth for the switch's copy — role_form.html renders
            # this help_text verbatim rather than hand-copying it into the template.
            self.fields['shared_with_managed'].help_text = _(
                "Managed tenants can assign this role to their own members; only "
                "you can edit it."
            )

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

        # Crispy layout — the matrix sections render through {{ form.matrix_grouped_items }},
        # so we only need to lay out the meta fields here.
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        layout_fields = ['name', 'description']
        if 'shared_with_managed' in self.fields:
            layout_fields.append('shared_with_managed')
        self.helper.layout = Layout(*layout_fields)
        add_standard_buttons(self.helper, self.instance, 'organization:role_list')

    # ------------------------------------------------------------------ cleaning
    def clean(self):
        cleaned_data = super().clean()

        if self.owner_tenant is None:
            raise forms.ValidationError(
                _("No tenant context: open this form from a tenant (?tenant=…) "
                  "or with an active tenant.")
            )

        # Build permission set from the matrix + custom checkboxes.
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

        # If any permission is granted, also auto-grant the dashboard perms needed for a
        # functioning landing page (view + create/customize own dashboards). Deliberately NOT
        # delete_dashboard: it isn't needed for a landing page, and auto-adding it would make
        # every non-empty role carry a delete_* permission — breaking role presets like
        # "Technician" whose whole point is to exclude delete_*.
        if assigned_perms:
            assigned_perms |= {
                'extras.view_dashboard', 'extras.change_dashboard',
                'extras.add_dashboard',
            }

        # Filter against the live permission table to drop codenames that don't exist
        # (matrix rows for models lacking that action, uninstalled plugins, etc.).
        valid = set(
            f'{p.content_type.app_label}.{p.codename}'
            for p in Permission.objects.select_related('content_type').all()
        )
        assigned_perms = {p for p in assigned_perms if p in valid}

        self.instance.tenant = self.owner_tenant

        # Privilege-escalation guard: a non-superuser may not assign permissions they do
        # not themselves hold in the role's owning tenant.
        validate_permission_grant(self.current_user, assigned_perms, self.owner_tenant)

        self.instance.permissions = sorted(assigned_perms)
        return cleaned_data

    # ---------------------------------------------------------------- template helpers
    @property
    def preset_definitions(self):
        """Built-in preset choices offered by the client-side preset picker.

        Returns a list of ``(value, label)`` pairs. ``value`` keys into
        ``preset_field_map``; ``blank`` is always first (clears the grid). Kept in
        sync with the seed's role catalog (``_seed/access.py``): Administrator = all,
        Technician = all non-delete op perms, Read-Only = all view_*.
        """
        return [
            ('blank', _('Blank (start from scratch)')),
            ('administrator', _('Administrator (full access)')),
            ('technician', _('Technician (all except delete)')),
            ('readonly', _('Read-Only (view only)')),
        ]

    @property
    def preset_field_map(self):
        """Map each preset to the matrix checkbox field names it pre-checks.

        Computed over *this form's* matrix models only (so presets stay scoped to
        the grid actually rendered — dropped plugin rows never appear). The values
        are matrix field names (``perm_<key>_<action>``), never permission
        codenames, so the client only toggles checkboxes and the server-side
        escalation guard in ``clean()`` still validates the final grant. Selecting
        a preset is a convenience only and never bypasses that guard.
        """
        administrator, technician, readonly = [], [], []
        for key in MATRIX_MODELS:
            for action in ('read', 'create', 'edit', 'delete'):
                fname = f'perm_{key}_{action}'
                administrator.append(fname)
                if action != 'delete':
                    technician.append(fname)
                if action == 'read':
                    readonly.append(fname)
        return {
            'blank': [],
            'administrator': administrator,
            'technician': technician,
            'readonly': readonly,
        }

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


class RoleFilterForm(FilterForm):
    from ..filters import RoleFilterSet  # inline import: breaks forms <-> filters cycle at import time
    filterset_class = RoleFilterSet


class RoleAssignUsersForm(forms.Form):
    """Bulk-add users to a Role (used by the "Assign Users" action).

    The view creates memberships (get_or_create) at the role's owning tenant plus
    own-reach ``RoleAssignment`` rows for the selected users.
    """
    users = forms.ModelMultipleChoiceField(
        queryset=None,
        required=True,
        label=_("Users"),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['users'].queryset = get_user_model().objects.order_by('username')
