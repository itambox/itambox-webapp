from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Row, Column

from core.forms import FilterForm
from extras.models import Tag

from ..models import Contact
from ..filters import ContactFilterSet


from extras.customfields import CustomFieldModelFormMixin

class ContactForm(CustomFieldModelFormMixin, forms.ModelForm):
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = Contact
        fields = ['name', 'title', 'phone', 'email', 'web_url', 'description', 'comments', 'tags']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Sales Director'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. +1 (555) 019-2834'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'e.g. contact@example.com'}),
            'web_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'e.g. https://support.example.com'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'comments': forms.Textarea(attrs={'class': 'form-control', 'rows': 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            Row(
                Column('name', css_class='form-group col-md-6 mb-0'),
                Column('title', css_class='form-group col-md-6 mb-0'),
                css_class='mb-3'
            ),
            Row(
                Column('phone', css_class='form-group col-md-4 mb-0'),
                Column('email', css_class='form-group col-md-4 mb-0'),
                Column('web_url', css_class='form-group col-md-4 mb-0'),
                css_class='mb-3'
            ),
            'description',
            'comments',
            'tags'
        )
        self.append_custom_fields_to_layout()
        from .helpers import add_standard_buttons
        add_standard_buttons(self.helper, self.instance, 'organization:contact_list')


class ContactFilterForm(FilterForm):
    filterset_class = ContactFilterSet
