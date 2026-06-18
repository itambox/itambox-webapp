from django import forms
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout

from core.forms import FilterForm
from extras.models import Tag

from ..models import TenantGroup
from ..filters import TenantGroupFilterSet


class TenantGroupForm(forms.ModelForm):
    parent = forms.ModelChoiceField(
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
        model = TenantGroup
        fields = ['name', 'slug', 'parent', 'description', 'tags']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control'}),
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
        self.helper.layout = Layout(
            'name', 'slug', 'parent', 'description', 'tags'
        )
        from .helpers import add_standard_buttons
        add_standard_buttons(self.helper, self.instance, 'organization:tenantgroup_list')

    def clean_parent(self):
        parent = self.cleaned_data.get('parent')
        if parent and self.instance and self.instance.pk and parent.pk == self.instance.pk:
            raise forms.ValidationError(_("A tenant group cannot be its own parent."))
        return parent


class TenantGroupFilterForm(FilterForm):
    filterset_class = TenantGroupFilterSet
