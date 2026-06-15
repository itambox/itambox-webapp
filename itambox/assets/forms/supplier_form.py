from django import forms
from django.urls import reverse
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Div

from core.forms import SlugModelForm
from ..models import Supplier


from extras.customfields import CustomFieldModelFormMixin

class SupplierForm(CustomFieldModelFormMixin, SlugModelForm):
    class Meta:
        model = Supplier
        fields = ['name', 'slug', 'website', 'address', 'notes', 'tags']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control', 'slugify': 'name'}),
            'website': forms.URLInput(attrs={'class': 'form-control'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'tags': forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cancel_url = reverse('assets:supplier_list')
        self.helper.layout = Layout(
            Div(
                Div('name', css_class='col-md-6'),
                Div('slug', css_class='col-md-6'),
                css_class='row',
            ),
            'website',
            'address',
            'notes',
            'tags',
            *self.action_buttons(cancel_url),
        )
