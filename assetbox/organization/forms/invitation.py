from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout
from ..models import TenantInvitation, TenantRole

class TenantInvitationForm(forms.ModelForm):
    role = forms.ChoiceField(
        choices=TenantRole.choices,
        widget=forms.Select(attrs={'class': 'form-select'}),
        initial=TenantRole.MEMBER
    )

    class Meta:
        model = TenantInvitation
        fields = ['email', 'role']
        widgets = {
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'user@example.com'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'email', 'role'
        )
        from .helpers import add_standard_buttons
        add_standard_buttons(self.helper, self.instance, 'dashboard')
