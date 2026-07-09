from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout
from core.auth.guards import validate_permission_grant
from ..models import TenantInvitation, Role

class TenantInvitationForm(forms.ModelForm):
    class Meta:
        model = TenantInvitation
        fields = ['email', 'role']
        widgets = {
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'user@example.com'}),
            'role': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        self._tenant = kwargs.pop('tenant', None)
        self._requesting_user = kwargs.pop('requesting_user', None)
        super().__init__(*args, **kwargs)
        if self._tenant:
            self.fields['role'].queryset = Role.objects.filter(tenant=self._tenant)
        else:
            self.fields['role'].queryset = Role.objects.none()

        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'email', 'role'
        )
        from .helpers import add_standard_buttons
        add_standard_buttons(self.helper, self.instance, 'dashboard')

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get('role')
        # Privilege-escalation guard: the inviter cannot grant, via the invited role, any
        # permission they do not themselves hold in this tenant. Without this a user with
        # only ``organization.add_tenantinvitation`` could invite an address they control,
        # pick the tenant's Administrator role, accept, and become admin.
        if role is not None:
            validate_permission_grant(
                self._requesting_user, role.permissions or [], self._tenant
            )
        return cleaned
