from django import forms
from django.urls import reverse

from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Div

from assets.models import AssetReservation


class AssetReservationForm(forms.ModelForm):
    start_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        required=True,
    )
    end_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        required=True,
    )

    class Meta:
        model = AssetReservation
        fields = [
            'asset',
            'reserved_for',
            'start_date',
            'end_date',
            'status',
            'purpose',
            'notes',
        ]
        widgets = {
            'asset': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'reserved_for': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'purpose': forms.TextInput(attrs={'class': 'form-control'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True

        button_text = 'Update' if self.instance and self.instance.pk else 'Create Reservation'
        try:
            cancel_url = reverse('assets:assetreservation_list')
        except Exception:
            cancel_url = '/'

        self.helper.layout = Layout(
            Div(
                Div('asset', css_class='col-md-6'),
                Div('reserved_for', css_class='col-md-6'),
                css_class='row',
            ),
            Div(
                Div('start_date', css_class='col-md-4'),
                Div('end_date', css_class='col-md-4'),
                Div('status', css_class='col-md-4'),
                css_class='row',
            ),
            'purpose',
            'notes',
            HTML('<div class="mt-3">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
            HTML('</div>'),
        )
