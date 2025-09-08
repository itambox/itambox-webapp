from django import forms
from django.urls import reverse
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, HTML, Row, Column

from core.forms import FilterForm
from extras.models import Tag

from ..models import AssetHolder, Tenant
from ..filters import AssetHolderFilterSet, AssetHolderAssignmentFilterSet



class AssetHolderForm(forms.ModelForm):
    tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'data-tomselect-tags': 'true'}),
    )

    class Meta:
        model = AssetHolder
        fields = [
            'first_name', 'last_name', 'upn', 'email', 'tenant',
            'description', 'comments', 'tags',
        ]
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'upn': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'comments': forms.Textarea(attrs={'class': 'form-control', 'rows': 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True

        button_text = 'Update' if self.instance and self.instance.pk else 'Create'
        cancel_url = reverse('organization:assetholder_list')

        self.helper.layout = Layout(
            Row(
                Column('first_name', css_class='form-group col-md-6 mb-0'),
                Column('last_name', css_class='form-group col-md-6 mb-0'),
                css_class='mb-3'
            ),
            Row(
                Column('upn', css_class='form-group col-md-6 mb-0'),
                Column('email', css_class='form-group col-md-6 mb-0'),
                css_class='mb-3'
            ),
            'tenant',
            'description',
            'comments',
            'tags',
            HTML('<div class="mt-4"></div>'),
            Submit('submit', button_text, css_class='btn btn-primary'),
            HTML(f'<a href="{cancel_url}" class="btn btn-outline-secondary ms-2">Cancel</a>'),
        )


class AssetHolderFilterForm(FilterForm):
    filterset_class = AssetHolderFilterSet

class AssetHolderAssignmentFilterForm(FilterForm):
    filterset_class = AssetHolderAssignmentFilterSet

