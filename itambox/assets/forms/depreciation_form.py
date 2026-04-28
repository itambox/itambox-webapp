from django import forms
from django.urls import reverse
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML

from ..models import Depreciation


class DepreciationForm(forms.ModelForm):
    class Meta:
        model = Depreciation
        fields = ['name', 'months']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'months': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True

        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = reverse('assets:depreciation_list')

        self.helper.layout = Layout(
            'name',
            'months',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )
