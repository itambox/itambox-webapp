from django import forms
from django.urls import reverse
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Row, Column, Fieldset, Button

from core.forms import ColorFieldFormMixin
from extras.models import Tag
from ..models import StatusLabel


class StatusLabelForm(ColorFieldFormMixin, forms.ModelForm):
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'data-tomselect-tags': 'true'}),
    )

    class Meta:
        model = StatusLabel
        fields = ['name', 'slug', 'type', 'description', 'color', 'tags']

    color = forms.CharField(
        max_length=7,
        required=False,
        widget=forms.TextInput(attrs={
            'type': 'color',
            'class': 'form-control form-control-color'
        }),
        help_text="Choose a color for this Status Label"
    )

    type = forms.ChoiceField(
        choices=StatusLabel.TYPE_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        cancel_url = reverse('assets:statuslabel_list')
        self.helper.layout = Layout(
            Fieldset(
                '',
                'name',
                'slug',
                'type',
                'description',
                'color',
                'tags',
            ),
            Row(
                Column(Submit('submit', 'Save', css_class='btn btn-primary'), css_class='col'),
                Column(Button('cancel', 'Cancel', css_class='btn btn-secondary', onclick=f"window.location.href='{cancel_url}'"), css_class='col text-end')
            )
        )
        self.fields['slug'].widget.attrs['slugify'] = 'name'
