from django import forms
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout
from core.forms import FilterForm
from core.auth.guards import validate_permission_grant
from ..models import TenantRole, Tenant
from ..filters import TenantRoleFilterSet

MATRIX_MODELS = {
    # Inventory & Hardware
    'asset': {
        'label': _('Assets'),
        'app': 'assets',
        'model_name': 'asset',
        'group': _('Inventory & Hardware'),
    },
    'assetrequest': {
        'label': _('Asset Requests'),
        'app': 'assets',
        'model_name': 'assetrequest',
        'group': _('Inventory & Hardware'),
    },
    'purchaseorder': {
        'label': _('Purchase Orders'),
        'app': 'procurement',
        'model_name': 'purchaseorder',
        'group': _('Inventory & Hardware'),
    },
    'auditsession': {
        'label': _('Audit Sessions'),
        'app': 'compliance',
        'model_name': 'auditsession',
        'group': _('Compliance & Custody'),
    },
    'assetaudit': {
        'label': _('Asset Audits'),
        'app': 'compliance',
        'model_name': 'assetaudit',
        'group': _('Compliance & Custody'),
    },
    'accessory': {
        'label': _('Accessories'),
        'app': 'inventory',
        'model_name': 'accessory',
        'group': _('Inventory & Hardware'),
    },
    'consumable': {
        'label': _('Consumables'),
        'app': 'inventory',
        'model_name': 'consumable',
        'group': _('Inventory & Hardware'),
    },
    'kit': {
        'label': _('Kits'),
        'app': 'inventory',
        'model_name': 'kit',
        'group': _('Inventory & Hardware'),
    },
    'component': {
        'label': _('Components'),
        'app': 'inventory',
        'model_name': 'component',
        'group': _('Inventory & Hardware'),
    },

    # Software & Subscriptions
    'license': {
        'label': _('Licenses'),
        'app': 'licenses',
        'model_name': 'license',
        'group': _('Software & Subscriptions'),
    },
    'software': {
        'label': _('Software'),
        'app': 'software',
        'model_name': 'software',
        'group': _('Software & Subscriptions'),
    },
    'subscription': {
        'label': _('Subscriptions'),
        'app': 'subscriptions',
        'model_name': 'subscription',
        'group': _('Software & Subscriptions'),
    },
    'subscriptionassignment': {
        'label': _('Subscription Assignments'),
        'app': 'subscriptions',
        'model_name': 'subscriptionassignment',
        'group': _('Software & Subscriptions'),
    },


    # Organization & Structure
    'location': {
        'label': _('Locations'),
        'app': 'organization',
        'model_name': 'location',
        'group': _('Organization & Structure'),
    },
    'site': {
        'label': _('Sites'),
        'app': 'organization',
        'model_name': 'site',
        'group': _('Organization & Structure'),
    },
    'assetholder': {
        'label': _('Asset Holders'),
        'app': 'organization',
        'model_name': 'assetholder',
        'group': _('Organization & Structure'),
    },
    'tenantrole': {
        'label': _('Roles & Permissions'),
        'app': 'organization',
        'model_name': 'tenantrole',
        'group': _('Organization & Structure'),
    },
    'region': {
        'label': _('Regions'),
        'app': 'organization',
        'model_name': 'region',
        'group': _('Organization & Structure'),
    },
    'sitegroup': {
        'label': _('Site Groups'),
        'app': 'organization',
        'model_name': 'sitegroup',
        'group': _('Organization & Structure'),
    },
    'tenantgroup': {
        'label': _('Tenant Groups'),
        'app': 'organization',
        'model_name': 'tenantgroup',
        'group': _('Organization & Structure'),
    },
    'contact': {
        'label': _('Contacts'),
        'app': 'organization',
        'model_name': 'contact',
        'group': _('Organization & Structure'),
    },
    'contactrole': {
        'label': _('Contact Roles'),
        'app': 'organization',
        'model_name': 'contactrole',
        'group': _('Organization & Structure'),
    },
    'tenantinvitation': {
        'label': _('Tenant Invitations'),
        'app': 'organization',
        'model_name': 'tenantinvitation',
        'group': _('Organization & Structure'),
    },

    # Metadata & Settings
    'manufacturer': {
        'label': _('Manufacturers'),
        'app': 'assets',
        'model_name': 'manufacturer',
        'group': _('Metadata & Settings'),
    },
    'supplier': {
        'label': _('Suppliers (Hardware)'),
        'app': 'assets',
        'model_name': 'supplier',
        'group': _('Metadata & Settings'),
    },
    'provider': {
        'label': _('Providers (Subscription)'),
        'app': 'subscriptions',
        'model_name': 'provider',
        'group': _('Metadata & Settings'),
    },
    'statuslabel': {
        'label': _('Status Labels'),
        'app': 'assets',
        'model_name': 'statuslabel',
        'group': _('Metadata & Settings'),
    },
    'category': {
        'label': _('Categories'),
        'app': 'assets',
        'model_name': 'category',
        'group': _('Metadata & Settings'),
    },
    'depreciation': {
        'label': _('Depreciation Schedules'),
        'app': 'assets',
        'model_name': 'depreciation',
        'group': _('Metadata & Settings'),
    },
    'assettype': {
        'label': _('Asset Types'),
        'app': 'assets',
        'model_name': 'assettype',
        'group': _('Metadata & Settings'),
    },
    'customfield': {
        'label': _('Custom Fields'),
        'app': 'extras',
        'model_name': 'customfield',
        'group': _('Metadata & Settings'),
    },
    'tag': {
        'label': _('Tags'),
        'app': 'extras',
        'model_name': 'tag',
        'group': _('Metadata & Settings'),
    },
    # System & Reporting
    'reporttemplate': {
        'label': _('Report Templates'),
        'app': 'extras',
        'model_name': 'reporttemplate',
        'group': _('System & Reporting'),
    },
    'scheduledreport': {
        'label': _('Scheduled Reports'),
        'app': 'extras',
        'model_name': 'scheduledreport',
        'group': _('System & Reporting'),
    },
    'alertrule': {
        'label': _('Alert Rules'),
        'app': 'extras',
        'model_name': 'alertrule',
        'group': _('System & Reporting'),
    },
    'alertlog': {
        'label': _('Alert Logs'),
        'app': 'extras',
        'model_name': 'alertlog',
        'group': _('System & Reporting'),
    },
    'notificationchannel': {
        'label': _('Notification Channels'),
        'app': 'extras',
        'model_name': 'notificationchannel',
        'group': _('System & Reporting'),
    },
    'exporttemplate': {
        'label': _('Export Templates'),
        'app': 'extras',
        'model_name': 'exporttemplate',
        'group': _('System & Reporting'),
    },
    'webhookendpoint': {
        'label': _('Webhook Endpoints'),
        'app': 'extras',
        'model_name': 'webhookendpoint',
        'group': _('System & Reporting'),
    },
    'eventrule': {
        'label': _('Event Rules'),
        'app': 'extras',
        'model_name': 'eventrule',
        'group': _('System & Reporting'),
    },
    'labeltemplate': {
        'label': _('Label Templates'),
        'app': 'extras',
        'model_name': 'labeltemplate',
        'group': _('System & Reporting'),
    },
    'recyclebin': {
        'label': _('Recycle Bin'),
        'app': 'core',
        'model_name': 'recyclebin',
        'group': _('System & Reporting'),
    },

    # Compliance & Custody
    'custodytemplate': {
        'label': _('Custody Templates'),
        'app': 'compliance',
        'model_name': 'custodytemplate',
        'group': _('Compliance & Custody'),
    },
    'custodyreceipt': {
        'label': _('Custody Receipts'),
        'app': 'compliance',
        'model_name': 'custodyreceipt',
        'group': _('Compliance & Custody'),
    },
    'assetmaintenance': {
        'label': _('Asset Maintenances'),
        'app': 'assets',
        'model_name': 'assetmaintenance',
        'group': _('Inventory & Hardware'),
    },

    'user': {
        'label': _('Users'),
        'app': 'auth',
        'model_name': 'user',
        'group': _('User Management'),
    },
    'token': {
        'label': _('API Tokens'),
        'app': 'users',
        'model_name': 'token',
        'group': _('User Management'),
    },

    # Additional domain models
    'warranty': {
        'label': _('Warranties'),
        'app': 'assets',
        'model_name': 'warranty',
        'group': _('Inventory & Hardware'),
    },
    'assetdisposal': {
        'label': _('Asset Disposals'),
        'app': 'assets',
        'model_name': 'assetdisposal',
        'group': _('Inventory & Hardware'),
    },
    'assetreservation': {
        'label': _('Asset Reservations'),
        'app': 'assets',
        'model_name': 'assetreservation',
        'group': _('Inventory & Hardware'),
    },
    'contract': {
        'label': _('Contracts'),
        'app': 'procurement',
        'model_name': 'contract',
        'group': _('Inventory & Hardware'),
    },
    'installedsoftware': {
        'label': _('Installed Software'),
        'app': 'software',
        'model_name': 'installedsoftware',
        'group': _('Software & Subscriptions'),
    },
    'assetrole': {
        'label': _('Asset Roles'),
        'app': 'assets',
        'model_name': 'assetrole',
        'group': _('Metadata & Settings'),
    },
    'costcenter': {
        'label': _('Cost Centers'),
        'app': 'organization',
        'model_name': 'costcenter',
        'group': _('Organization & Structure'),
    },
    'tenantmembership': {
        'label': _('Tenant Assignments'),
        'app': 'organization',
        'model_name': 'tenantmembership',
        'group': _('Organization & Structure'),
    },
    'journalentry': {
        'label': _('Journal Entries'),
        'app': 'extras',
        'model_name': 'journalentry',
        'group': _('System & Reporting'),
    },
    'configcontext': {
        'label': _('Config Contexts'),
        'app': 'extras',
        'model_name': 'configcontext',
        'group': _('System & Reporting'),
    },

    # Plugins
    'docusignenvelope': {
        'label': _('DocuSign Envelopes'),
        'app': 'itambox_esign',
        'model_name': 'docusignenvelope',
        'group': _('Plugins'),
    },
}

# Plugin-provided rows only make sense when the plugin is installed; otherwise their
# permission codenames don't exist and the whitelist validation would reject them.
from django.apps import apps as _django_apps  # noqa: E402

for _plugin_key, _plugin_app in (('docusignenvelope', 'itambox_esign'),):
    if not _django_apps.is_installed(_plugin_app):
        MATRIX_MODELS.pop(_plugin_key, None)

class TenantRoleForm(forms.ModelForm):
    tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.none(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = TenantRole
        fields = ['name', 'tenant', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., Inventory Manager'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Describe the role...'}),
        }

    def __init__(self, *args, **kwargs):
        self.current_user = kwargs.pop('user', None)
        self.tenant = kwargs.pop('tenant', None)
        super().__init__(*args, **kwargs)

        # Configure tenant field based on context
        if self.instance.pk:
            self.fields['tenant'].widget = forms.HiddenInput()
            self.fields['tenant'].required = False
            self.fields['tenant'].initial = self.instance.tenant
            self.fields['tenant'].queryset = Tenant.objects.filter(pk=self.instance.tenant.pk)
        elif self.tenant:
            self.fields['tenant'].widget = forms.HiddenInput()
            self.fields['tenant'].required = False
            self.fields['tenant'].initial = self.tenant
            self.fields['tenant'].queryset = Tenant.objects.filter(pk=self.tenant.pk)
            if not getattr(self.instance, 'tenant', None):
                self.instance.tenant = self.tenant
        else:
            self.fields['tenant'].queryset = Tenant.objects.all()
            self.fields['tenant'].required = True

        # Add matrix fields dynamically
        for key, info in MATRIX_MODELS.items():
            self.fields[f'perm_{key}_read'] = forms.BooleanField(
                required=False,
                widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
            )
            self.fields[f'perm_{key}_create'] = forms.BooleanField(
                required=False,
                widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
            )
            self.fields[f'perm_{key}_edit'] = forms.BooleanField(
                required=False,
                widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
            )
            self.fields[f'perm_{key}_delete'] = forms.BooleanField(
                required=False,
                widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
            )

            # Pre-check from the instance's permission set (existing role being
            # edited, or an unsaved clone carrying the source role's permissions).
            if self.instance.permissions:
                app = info['app']
                model = info['model_name']
                self.fields[f'perm_{key}_read'].initial = f'{app}.view_{model}' in self.instance.permissions
                self.fields[f'perm_{key}_create'].initial = f'{app}.add_{model}' in self.instance.permissions
                self.fields[f'perm_{key}_edit'].initial = f'{app}.change_{model}' in self.instance.permissions
                self.fields[f'perm_{key}_delete'].initial = f'{app}.delete_{model}' in self.instance.permissions

        # Add custom delegated asset request permission
        self.fields['perm_add_delegated_assetrequest'] = forms.BooleanField(
            required=False,
            widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
        )
        if self.instance.permissions:
            self.fields['perm_add_delegated_assetrequest'].initial = 'assets.add_delegated_assetrequest' in self.instance.permissions

        # Add custom purchase order permissions
        self.fields['perm_receive_purchaseorder'] = forms.BooleanField(
            required=False,
            widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
        )
        self.fields['perm_approve_purchaseorder'] = forms.BooleanField(
            required=False,
            widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
        )
        if self.instance.permissions:
            self.fields['perm_receive_purchaseorder'].initial = 'procurement.receive_purchaseorder' in self.instance.permissions
            self.fields['perm_approve_purchaseorder'].initial = 'procurement.approve_purchaseorder' in self.instance.permissions

        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        
        layout_fields = ['name']
        if not self.tenant and not self.instance.pk:
            layout_fields.append('tenant')
        layout_fields.append('description')
        
        self.helper.layout = Layout(*layout_fields)
        from .helpers import add_standard_buttons
        add_standard_buttons(self.helper, self.instance, 'organization:tenantrole_list')

    def clean(self):
        cleaned_data = super().clean()
        assigned_perms = set()

        for key, info in MATRIX_MODELS.items():
            app = info['app']
            model = info['model_name']

            if cleaned_data.get(f'perm_{key}_read'):
                assigned_perms.add(f'{app}.view_{model}')
            if cleaned_data.get(f'perm_{key}_create'):
                assigned_perms.add(f'{app}.add_{model}')
            if cleaned_data.get(f'perm_{key}_edit'):
                assigned_perms.add(f'{app}.change_{model}')
            if cleaned_data.get(f'perm_{key}_delete'):
                assigned_perms.add(f'{app}.delete_{model}')

        if cleaned_data.get('perm_add_delegated_assetrequest'):
            assigned_perms.add('assets.add_delegated_assetrequest')

        if cleaned_data.get('perm_receive_purchaseorder'):
            assigned_perms.add('procurement.receive_purchaseorder')
        if cleaned_data.get('perm_approve_purchaseorder'):
            assigned_perms.add('procurement.approve_purchaseorder')

        # If any permission is set, also grant dashboard viewing/extras permissions
        if assigned_perms:
            assigned_perms.add('extras.view_dashboard')
            assigned_perms.add('extras.change_dashboard')
            assigned_perms.add('extras.add_dashboard')
            assigned_perms.add('extras.delete_dashboard')

        # Keep only real permission codenames. The matrix is built by us, so an
        # invalid codename means a matrix row references a permission/action that does
        # not exist — e.g. a model without an 'add' permission (RecycleBin) or an
        # uninstalled plugin. Drop those silently rather than blocking the whole save
        # on what is our own configuration, not user input. (App-label typos are
        # caught in dev by the matrix audit, not by failing the user here.)
        from django.contrib.auth.models import Permission
        all_codenames = set(
            f"{p.content_type.app_label}.{p.codename}"
            for p in Permission.objects.select_related('content_type').all()
        )
        assigned_perms = {p for p in assigned_perms if p in all_codenames}

        # Set tenant automatically from form field or kwarg/instance. Resolved BEFORE
        # the escalation guard, which is evaluated against the granting user's effective
        # permissions in this role's own tenant.
        tenant = cleaned_data.get('tenant')
        if tenant:
            self.instance.tenant = tenant
        elif self.tenant:
            self.instance.tenant = self.tenant
        elif self.instance.pk and getattr(self.instance, 'tenant', None):
            # Fallback to existing tenant when editing
            pass

        if self.instance.tenant:
            cleaned_data['tenant'] = self.instance.tenant
        else:
            raise forms.ValidationError(_("Tenant assignment is required."))

        # Privilege escalation check: a non-superuser cannot assign permissions they do
        # not themselves hold in this tenant. Centralised in core.auth.guards so the same
        # rule applies to serializers / SCIM / model hooks.
        validate_permission_grant(self.current_user, assigned_perms, self.instance.tenant)

        self.instance.permissions = list(assigned_perms)
        return cleaned_data

    @property
    def matrix_items(self):
        items = []
        for key, info in MATRIX_MODELS.items():
            items.append({
                'key': key,
                'label': info['label'],
                'read_field': self[f'perm_{key}_read'],
                'create_field': self[f'perm_{key}_create'],
                'edit_field': self[f'perm_{key}_edit'],
                'delete_field': self[f'perm_{key}_delete'],
            })
        return items

    @property
    def matrix_grouped_items(self):
        groups = {}
        for key, info in MATRIX_MODELS.items():
            group_name = info.get('group', 'Other')
            if group_name not in groups:
                groups[group_name] = []
            groups[group_name].append({
                'key': key,
                'label': info['label'],
                'read_field': self[f'perm_{key}_read'],
                'create_field': self[f'perm_{key}_create'],
                'edit_field': self[f'perm_{key}_edit'],
                'delete_field': self[f'perm_{key}_delete'],
            })
        return groups


class TenantRoleFilterForm(FilterForm):
    filterset_class = TenantRoleFilterSet
