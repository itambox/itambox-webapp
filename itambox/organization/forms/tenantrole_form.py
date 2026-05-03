from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout
from ..models import TenantRole

MATRIX_MODELS = {
    'asset': {
        'label': 'Assets',
        'app': 'assets',
        'model_name': 'asset',
    },
    'accessory': {
        'label': 'Accessories',
        'app': 'inventory',
        'model_name': 'accessory',
    },
    'consumable': {
        'label': 'Consumables',
        'app': 'inventory',
        'model_name': 'consumable',
    },
    'kit': {
        'label': 'Kits',
        'app': 'inventory',
        'model_name': 'kit',
    },
    'component': {
        'label': 'Components',
        'app': 'components',
        'model_name': 'component',
    },
    'location': {
        'label': 'Locations',
        'app': 'organization',
        'model_name': 'location',
    },
    'site': {
        'label': 'Sites',
        'app': 'organization',
        'model_name': 'site',
    },
    'assetholder': {
        'label': 'Asset Holders',
        'app': 'organization',
        'model_name': 'assetholder',
    },
}

class TenantRoleForm(forms.ModelForm):
    class Meta:
        model = TenantRole
        fields = ['name', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., Inventory Manager'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Describe the role...'}),
        }

    def __init__(self, *args, **kwargs):
        self.current_user = kwargs.pop('user', None)
        self.tenant = kwargs.pop('tenant', None)
        super().__init__(*args, **kwargs)

        # Add matrix fields dynamically
        for key, info in MATRIX_MODELS.items():
            self.fields[f'perm_{key}_read'] = forms.BooleanField(
                required=False,
                widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
            )
            self.fields[f'perm_{key}_write'] = forms.BooleanField(
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
                self.fields[f'perm_{key}_write'].initial = (
                    f'{app}.add_{model}' in self.instance.permissions and 
                    f'{app}.change_{model}' in self.instance.permissions
                )
                self.fields[f'perm_{key}_delete'].initial = f'{app}.delete_{model}' in self.instance.permissions

        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'name',
            'description',
        )
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
            if cleaned_data.get(f'perm_{key}_write'):
                assigned_perms.add(f'{app}.add_{model}')
                assigned_perms.add(f'{app}.change_{model}')
            if cleaned_data.get(f'perm_{key}_delete'):
                assigned_perms.add(f'{app}.delete_{model}')

        # If any read/write/delete permission is set, also grant dashboard viewing/extras permissions
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
        
        # Set tenant automatically from kwarg or existing instance
        if self.tenant:
            self.instance.tenant = self.tenant
            
        return cleaned_data
