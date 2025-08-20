from django import forms
from django.urls import reverse
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML

from core.forms import SlugModelForm
from extras.models import Tag
from ..models import Manufacturer


class ManufacturerForm(SlugModelForm):
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'data-tomselect-tags': 'true'}),
    )

    class Meta:
        model = Manufacturer
        fields = ['name', 'slug', 'description', 'tags']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control', 'slugify': 'name'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }
        help_texts = {
            'slug': 'URL-friendly identifier.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.fields['slug'].widget.attrs['slugify'] = 'name'

        button_text = 'Update' if self.instance and self.instance.pk else 'Create'
        self.helper.layout = Layout(
            'name',
            'slug',
            'description',
            'tags',
            HTML('<div class="mt-4">'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML('<a href="{0}" class="btn btn-outline-secondary ms-2">Cancel</a>'.format(reverse('assets:manufacturer_list'))),
            HTML('</div>')
        )
