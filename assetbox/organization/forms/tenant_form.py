from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout

from core.forms import FilterForm
from extras.models import Tag

from ..models import Tenant, TenantGroup
from ..filters import TenantFilterSet


class TenantForm(forms.ModelForm):
    group = forms.ModelChoiceField(
        queryset=TenantGroup.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = Tenant
        fields = ['name', 'slug', 'group', 'description', 'comments', 'tags']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'comments': forms.Textarea(attrs={'class': 'form-control', 'rows': 5}),
        }
        help_texts = {
            'slug': 'URL-friendly identifier.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'name', 'slug', 'group', 'description', 'comments', 'tags'
        )
        from .helpers import add_standard_buttons
        add_standard_buttons(self.helper, self.instance, 'organization:tenant_list')


class TenantFilterForm(FilterForm):
    filterset_class = TenantFilterSet
