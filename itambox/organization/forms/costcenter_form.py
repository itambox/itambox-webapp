from django import forms
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Div

from core.forms import FilterForm
from extras.customfields import CustomFieldModelFormMixin

from ..models import CostCenter, Tenant
from ..filters import CostCenterFilterSet


class CostCenterForm(CustomFieldModelFormMixin, forms.ModelForm):
    parent = forms.ModelChoiceField(
        queryset=CostCenter.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = CostCenter
        fields = ['name', 'code', 'slug', 'tenant', 'parent', 'description', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control'}),
            'code': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        help_texts = {
            'slug': 'URL-friendly identifier.',
            'code': 'Short unique code within this tenant (e.g. "CC-100").',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            Div(
                Div('name', css_class='col-md-6'),
                Div('code', css_class='col-md-3'),
                Div('slug', css_class='col-md-3'),
                css_class='row'
            ),
            Div(
                Div('tenant', css_class='col-md-6'),
                Div('parent', css_class='col-md-6'),
                css_class='row'
            ),
            'description',
            'is_active',
        )
        self.append_custom_fields_to_layout()
        from .helpers import add_standard_buttons
        add_standard_buttons(self.helper, self.instance, 'organization:costcenter_list')

    def clean_parent(self):
        parent = self.cleaned_data.get('parent')
        if parent and self.instance and self.instance.pk:
            if parent.pk == self.instance.pk:
                raise forms.ValidationError(_("A cost center cannot be its own parent."))
            # Cycle check: walk up from the proposed parent; if we reach self, it's a cycle.
            visited = set()
            node = parent
            while node is not None:
                if node.pk == self.instance.pk:
                    raise forms.ValidationError(
                        _("Setting this parent would create a cycle in the hierarchy.")
                    )
                if node.pk in visited:
                    break
                visited.add(node.pk)
                node = node.parent
        return parent


class CostCenterFilterForm(FilterForm):
    filterset_class = CostCenterFilterSet
