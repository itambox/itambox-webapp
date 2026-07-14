from django import forms
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Div, HTML

from core.forms import FilterForm, scope_tenant_group_field
from extras.models import Tag
from itambox.middleware import get_current_user

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
        fields = ['name', 'slug', 'group', 'managed_by', 'is_provider', 'currency',
                  'default_depreciation', 'description', 'comments', 'tags']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control'}),
            'managed_by': forms.Select(attrs={'class': 'form-select'}),
            'is_provider': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'default_depreciation': forms.Select(attrs={'class': 'form-select', 'data-tom-select': ''}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'comments': forms.Textarea(attrs={'class': 'form-control', 'rows': 5}),
        }
        help_texts = {
            'slug': _('URL-friendly identifier.'),
        }

    def __init__(self, *args, **kwargs):
        # The management-tree fields (is_provider / managed_by) are superuser-only by
        # decision (2026-07-10). Views may pass the actor explicitly; fall back to the
        # request contextvar so plain generic views need no wiring.
        requesting_user = kwargs.pop('user', None) or get_current_user()
        # "Add managed tenant" (tenants/add/?managed_by=<pk>) from the MSP tenant's
        # Managed-Tenants tab: TenantEditView forwards the raw query-string value
        # here so it can be validated + forced server-side (see below) instead of
        # trusted as user input.
        managed_by_param = kwargs.pop('managed_by_param', None)
        super().__init__(*args, **kwargs)
        # Scope the tenant's group picker to the user's accessible groups.
        scope_tenant_group_field(self, field_name='group')
        # Preserve exotic codes set via the API: keep the saved value selectable
        # instead of silently dropping it on the next edit.
        current = getattr(self.instance, 'currency', None)
        if current and current not in dict(CURRENCY_CHOICES):
            self.fields['currency'].choices = list(CURRENCY_CHOICES) + [(current, current)]

        is_superuser = bool(requesting_user and getattr(requesting_user, 'is_superuser', False))

        # A provider admin creating a new managed tenant via the "Add managed
        # tenant" link is forced into that provider's managed_by -- but ONLY when
        # they hold organization.add_tenant on the (is_provider) target tenant.
        # Never honoured on edit: an existing tenant's managed_by isn't silently
        # reassigned by a stray query param.
        is_new = not self.instance.pk
        managed_by_candidate = None
        if is_new and managed_by_param:
            try:
                managed_by_id = int(managed_by_param)
            except (TypeError, ValueError):
                managed_by_id = None
            if managed_by_id is not None:
                managed_by_candidate = Tenant._base_manager.filter(
                    pk=managed_by_id, is_provider=True, deleted_at__isnull=True,
                ).first()

        forced_managed_by = None
        if (
            not is_superuser
            and managed_by_candidate is not None
            and requesting_user
            and requesting_user.has_perm(
                'organization.add_tenant', obj=managed_by_candidate,
            )
        ):
            forced_managed_by = managed_by_candidate

        if is_superuser:
            # Unscoped base manager: the managing-tenant picker must list every
            # is_provider tenant regardless of the active-tenant context.
            managed_by_qs = Tenant._base_manager.filter(
                is_provider=True, deleted_at__isnull=True,
            ).order_by('name')
            if self.instance.pk:
                managed_by_qs = managed_by_qs.exclude(pk=self.instance.pk)
            self.fields['managed_by'].queryset = managed_by_qs
            if is_new and managed_by_candidate is not None:
                self.fields['managed_by'].initial = managed_by_candidate.pk
        else:
            # Non-superusers never see (or write) the management-tree fields; popped
            # fields keep the instance's saved values untouched on edit. A forced
            # managed_by is set directly on the (unsaved) instance instead — never
            # through a bound field — so it can't be overridden via POST body.
            self.fields.pop('is_provider', None)
            self.fields.pop('managed_by', None)
            if forced_managed_by is not None:
                self.instance.managed_by = forced_managed_by

        self.helper = FormHelper(self)
        self.helper.form_method = 'post'
        self.helper.form_tag = True
        layout_rows = [
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
        ]
        if is_superuser:
            layout_rows.append(
                Div(
                    Div('managed_by', css_class='col-md-6'),
                    Div('is_provider', css_class='col-md-6 d-flex align-items-center'),
                    css_class='row'
                )
            )
        elif forced_managed_by is not None:
            layout_rows.append(HTML(format_html(
                '<div class="alert alert-info py-2 px-3 mb-3">{}</div>',
                _('This tenant will be managed by %(name)s.') % {'name': forced_managed_by.name},
            )))
        layout_rows.extend(['description', 'comments', 'tags'])
        self.helper.layout = Layout(*layout_rows)
        from .helpers import add_standard_buttons
        add_standard_buttons(self.helper, self.instance, 'organization:tenant_list')


class TenantFilterForm(FilterForm):
    filterset_class = TenantFilterSet
