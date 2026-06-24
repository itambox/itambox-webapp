from django import forms
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout

from core.forms import FilterForm

from ..models import Provider, ProviderRole, ProviderRoleTemplate, Tenant
from ..filters import ProviderFilterSet, ProviderRoleFilterSet, ProviderRoleTemplateFilterSet


# NOTE: `settings` (Provider) and `permissions` (ProviderRoleTemplate) are JSON
# fields intentionally excluded from these basic admin forms for now.


class ProviderForm(forms.ModelForm):
    internal_tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = Provider
        fields = ['name', 'slug', 'description', 'comments', 'internal_tenant']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'comments': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }
        help_texts = {
            'slug': _('URL-friendly identifier.'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'name', 'slug', 'description', 'comments', 'internal_tenant'
        )
        from .helpers import add_standard_buttons
        add_standard_buttons(self.helper, self.instance, 'organization:provider_list')


class ProviderFilterForm(FilterForm):
    filterset_class = ProviderFilterSet


class ProviderRoleTemplateForm(forms.ModelForm):
    provider = forms.ModelChoiceField(
        queryset=Provider.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = ProviderRoleTemplate
        fields = ['provider', 'name', 'description', 'is_default']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'is_default': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'provider', 'name', 'description', 'is_default'
        )
        from .helpers import add_standard_buttons
        add_standard_buttons(self.helper, self.instance, 'organization:providerroletemplate_list')


class ProviderRoleTemplateFilterForm(FilterForm):
    filterset_class = ProviderRoleTemplateFilterSet


class ProviderRoleForm(forms.ModelForm):
    provider = forms.ModelChoiceField(
        queryset=Provider.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    tenant_role_template = forms.ModelChoiceField(
        queryset=ProviderRoleTemplate.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = ProviderRole
        fields = [
            'provider', 'name', 'slug', 'description', 'tenant_role_template',
            'can_manage_tenants', 'can_manage_provider_users', 'can_manage_groups',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'can_manage_tenants': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_manage_provider_users': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_manage_groups': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        help_texts = {
            'slug': _('URL-friendly identifier.'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            'provider', 'name', 'slug', 'description', 'tenant_role_template',
            'can_manage_tenants', 'can_manage_provider_users', 'can_manage_groups',
        )
        from .helpers import add_standard_buttons
        add_standard_buttons(self.helper, self.instance, 'organization:providerrole_list')


class ProviderRoleFilterForm(FilterForm):
    filterset_class = ProviderRoleFilterSet
