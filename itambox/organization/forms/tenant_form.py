from django import forms
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Div

from core.forms import FilterForm
from extras.models import Tag

from ..models import Tenant, TenantGroup
from ..filters import TenantFilterSet


# Codes the `money` template filter renders with a proper symbol/placement;
# anything else falls back to an ISO-code suffix.
CURRENCY_CHOICES = [
    ('EUR', _('EUR — Euro (€)')),
    ('USD', _('USD — US Dollar ($)')),
    ('GBP', _('GBP — British Pound (£)')),
    ('CHF', _('CHF — Swiss Franc')),
    ('SEK', _('SEK — Swedish Krona')),
    ('NOK', _('NOK — Norwegian Krone')),
    ('DKK', _('DKK — Danish Krone')),
    ('CAD', _('CAD — Canadian Dollar')),
    ('AUD', _('AUD — Australian Dollar')),
    ('JPY', _('JPY — Japanese Yen (¥)')),
]


class TenantForm(forms.ModelForm):
    group = forms.ModelChoiceField(
        queryset=TenantGroup.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    currency = forms.ChoiceField(
        choices=CURRENCY_CHOICES,
        initial='EUR',
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text=_('ISO 4217 code used when displaying this tenant\'s monetary values (display only, no conversion).'),
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'data-tom-select': ''}),
    )

    class Meta:
        model = Tenant
        fields = ['name', 'slug', 'group', 'currency', 'default_depreciation', 'description', 'comments', 'tags']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control'}),
            'default_depreciation': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'comments': forms.Textarea(attrs={'class': 'form-control', 'rows': 5}),
        }
        help_texts = {
            'slug': _('URL-friendly identifier.'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Preserve exotic codes set via the API: keep the saved value selectable
        # instead of silently dropping it on the next edit.
        current = getattr(self.instance, 'currency', None)
        if current and current not in dict(CURRENCY_CHOICES):
            self.fields['currency'].choices = list(CURRENCY_CHOICES) + [(current, current)]
        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        self.helper.layout = Layout(
            Div(
                Div('name', css_class='col-md-6'),
                Div('slug', css_class='col-md-6'),
                css_class='row'
            ),
            Div(
                Div('group', css_class='col-md-6'),
                Div('currency', css_class='col-md-3'),
                Div('default_depreciation', css_class='col-md-3'),
                css_class='row'
            ),
            'description',
            'comments',
            'tags',
        )
        from .helpers import add_standard_buttons
        add_standard_buttons(self.helper, self.instance, 'organization:tenant_list')


class TenantFilterForm(FilterForm):
    filterset_class = TenantFilterSet
