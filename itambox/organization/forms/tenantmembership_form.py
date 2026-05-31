from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from ..models import Tenant, TenantRole, TenantMembership

User = get_user_model()

class TenantMembershipForm(forms.ModelForm):
    user = forms.ModelChoiceField(
        queryset=User.objects.all(),
        required=True,
        label=_("User"),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.all(),
        required=True,
        label=_("Tenant"),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    role = forms.ModelChoiceField(
        queryset=TenantRole.objects.all(),
        required=True,
        label=_("Role"),
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = TenantMembership
        fields = ['user', 'tenant', 'role']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'user', 'tenant', 'role'
        )
        from .helpers import add_standard_buttons
        add_standard_buttons(self.helper, self.instance, 'users:user_list')

    def clean(self):
        cleaned_data = super().clean()
        tenant = cleaned_data.get('tenant')
        role = cleaned_data.get('role')
        if tenant and role and role.tenant != tenant:
            raise forms.ValidationError(_("The selected role does not belong to the selected tenant."))
        return cleaned_data
