from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout
from ..models import TenantInvitation, TenantRole

class TenantInvitationForm(forms.ModelForm):
    class Meta:
        model = TenantInvitation
        fields = ['email', 'role']
        widgets = {
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'user@example.com'}),
            'role': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        tenant = kwargs.pop('tenant', None)
        super().__init__(*args, **kwargs)
        if tenant:
            self.fields['role'].queryset = TenantRole.objects.filter(tenant=tenant)
        else:
            self.fields['role'].queryset = TenantRole.objects.none()
            
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'email', 'role'
        )
        from .helpers import add_standard_buttons
        add_standard_buttons(self.helper, self.instance, 'dashboard')
