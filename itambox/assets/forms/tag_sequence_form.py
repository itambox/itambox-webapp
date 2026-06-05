from django import forms
from django.urls import reverse
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML

from ..models import AssetTagSequence


class AssetTagSequenceForm(forms.ModelForm):
    class Meta:
        model = AssetTagSequence
        fields = ['prefix', 'next_value', 'zero_padding', 'tenant', 'category', 'is_active']
        widgets = {
            'prefix': forms.TextInput(attrs={'class': 'form-control'}),
            'next_value': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'zero_padding': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'max': 20}),
            'tenant': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'category': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True

        button_text = 'Update' if self.instance.pk else 'Create'
        cancel_url = reverse('assets:assettagsequence_list')

        self.helper.layout = Layout(
            'prefix',
            'next_value',
            'zero_padding',
            'tenant',
            'category',
            'is_active',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>')
        )
