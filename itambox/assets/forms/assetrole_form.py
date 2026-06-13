from django import forms
from django.urls import reverse
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Row, Column, Fieldset

from core.forms import ColorFieldFormMixin
from ..models import AssetRole


class AssetRoleForm(ColorFieldFormMixin, forms.ModelForm):
    class Meta:
        model = AssetRole
        fields = ['name', 'slug', 'description', 'color', 'allows_components', 'tags']

    color = forms.CharField(
        max_length=7,
        required=False,
        widget=forms.TextInput(attrs={
            'type': 'color',
            'class': 'form-control form-control-color'
        }),
        help_text="Choose a color for this Asset Role"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        cancel_url = reverse('assets:assetrole_list')
        self.helper.layout = Layout(
            Fieldset(
                '',
                'name',
                'slug',
                'description',
                'color',
                'allows_components',
                'tags'
            ),
            Row(
                Column(Submit('submit', 'Save', css_class='btn btn-primary'), css_class='col'),
                Column(HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2" data-no-dirty-track="true">Cancel</a>'), css_class='col text-end')
            )
        )
