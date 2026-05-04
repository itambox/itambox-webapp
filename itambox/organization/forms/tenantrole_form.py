from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout
from ..models import TenantRole, Tenant

MATRIX_MODELS = {
    'asset': {
        'label': 'Assets',
        'app': 'assets',
        'model_name': 'asset',
        'group': 'Inventory & Hardware',
    },
    'auditsession': {
        'label': 'Audit Sessions',
        'app': 'assets',
        'model_name': 'auditsession',
        'group': 'Inventory & Hardware',
    },
    'assetaudit': {
        'label': 'Asset Audits',
        'app': 'assets',
        'model_name': 'assetaudit',
        'group': 'Inventory & Hardware',
    },
    'accessory': {
        'label': 'Accessories',
        'app': 'inventory',
        'model_name': 'accessory',
        'group': 'Inventory & Hardware',
    },
    'consumable': {
        'label': 'Consumables',
        'app': 'inventory',
        'model_name': 'consumable',
        'group': 'Inventory & Hardware',
    },
    'kit': {
        'label': 'Kits',
        'app': 'inventory',
        'model_name': 'kit',
        'group': 'Inventory & Hardware',
    },
    'component': {
        'label': 'Components',
        'app': 'components',
        'model_name': 'component',
        'group': 'Inventory & Hardware',
    },
    'license': {
        'label': 'Licenses',
        'app': 'licenses',
        'model_name': 'license',
        'group': 'Software & Subscriptions',
    },
    'software': {
        'label': 'Software',
        'app': 'software',
        'model_name': 'software',
        'group': 'Software & Subscriptions',
    },
    'subscription': {
        'label': 'Subscriptions',
        'app': 'subscriptions',
        'model_name': 'subscription',
        'group': 'Software & Subscriptions',
    },
    'location': {
        'label': 'Locations',
        'app': 'organization',
        'model_name': 'location',
        'group': 'Organization & Structure',
    },
    'site': {
        'label': 'Sites',
        'app': 'organization',
        'model_name': 'site',
        'group': 'Organization & Structure',
    },
    'assetholder': {
        'label': 'Asset Holders',
        'app': 'organization',
        'model_name': 'assetholder',
        'group': 'Organization & Structure',
    },
    'tenantrole': {
        'label': 'Roles & Permissions',
        'app': 'organization',
        'model_name': 'tenantrole',
        'group': 'Organization & Structure',
    },
    'manufacturer': {
        'label': 'Manufacturers',
        'app': 'assets',
        'model_name': 'manufacturer',
        'group': 'Metadata & Settings',
    },
    'supplier': {
        'label': 'Suppliers (Hardware)',
        'app': 'assets',
        'model_name': 'supplier',
        'group': 'Metadata & Settings',
    },
    'provider': {
        'label': 'Providers (Subscription)',
        'app': 'subscriptions',
        'model_name': 'provider',
        'group': 'Metadata & Settings',
    },
    'statuslabel': {
        'label': 'Status Labels',
        'app': 'assets',
        'model_name': 'statuslabel',
        'group': 'Metadata & Settings',
    },
    'category': {
        'label': 'Categories',
        'app': 'assets',
        'model_name': 'category',
        'group': 'Metadata & Settings',
    },
    'depreciation': {
        'label': 'Depreciation Schedules',
        'app': 'assets',
        'model_name': 'depreciation',
        'group': 'Metadata & Settings',
    },
    'assettype': {
        'label': 'Asset Types',
        'app': 'assets',
        'model_name': 'assettype',
        'group': 'Metadata & Settings',
    },
    'customfield': {
        'label': 'Custom Fields',
        'app': 'assets',
        'model_name': 'customfield',
        'group': 'Metadata & Settings',
    },
    'reporttemplate': {
        'label': 'Report Templates',
        'app': 'core',
        'model_name': 'reporttemplate',
        'group': 'System & Reporting',
    },
    'scheduledreport': {
        'label': 'Scheduled Reports',
        'app': 'core',
        'model_name': 'scheduledreport',
        'group': 'System & Reporting',
    },
}

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

            # Set initial values if modifying existing instance
            if self.instance.pk and self.instance.permissions:
                app = info['app']
                model = info['model_name']
                self.fields[f'perm_{key}_read'].initial = f'{app}.view_{model}' in self.instance.permissions
                self.fields[f'perm_{key}_create'].initial = f'{app}.add_{model}' in self.instance.permissions
                self.fields[f'perm_{key}_edit'].initial = f'{app}.change_{model}' in self.instance.permissions
                self.fields[f'perm_{key}_delete'].initial = f'{app}.delete_{model}' in self.instance.permissions

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

        # If any permission is set, also grant dashboard viewing/extras permissions
        if assigned_perms:
            assigned_perms.add('extras.view_dashboard')
            assigned_perms.add('extras.change_dashboard')
            assigned_perms.add('extras.add_dashboard')
            assigned_perms.add('extras.delete_dashboard')

        # Privilege escalation check: user cannot assign permissions they do not possess
        request_user = self.current_user
        if request_user and not request_user.is_superuser:
            escalated = [p for p in assigned_perms if not request_user.has_perm(p)]
            if escalated:
                raise forms.ValidationError(
                    f"Privilege escalation detected: You cannot assign the following permissions because you do not have them: {', '.join(escalated)}"
                )

        # Whitelist validation: check if permission codenames are valid in Django's Permission model
        from django.contrib.auth.models import Permission
        all_codenames = set()
        for p in Permission.objects.select_related('content_type').all():
            all_codenames.add(f"{p.content_type.app_label}.{p.codename}")

        invalid_perms = [p for p in assigned_perms if p not in all_codenames]
        if invalid_perms:
            raise forms.ValidationError(
                f"Invalid permission codenames: {', '.join(invalid_perms)}"
            )

        self.instance.permissions = list(assigned_perms)
        
        # Set tenant automatically from form field or kwarg/instance
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
            raise forms.ValidationError("Tenant assignment is required.")
            
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
