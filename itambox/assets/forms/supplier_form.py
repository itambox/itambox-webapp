from crispy_forms.layout import Div, Fieldset, Layout
from django import forms
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from core.forms import SlugModelForm, scope_tenant_field, scope_tenant_group_field
from extras.customfields import CustomFieldModelFormMixin
from organization.models import Tenant, TenantGroup

from ..models import Supplier


class SupplierForm(CustomFieldModelFormMixin, SlugModelForm):
    tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.all(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "data-tom-select": ""}),
        label=_("Tenant"),
    )
    tenant_group = forms.ModelChoiceField(
        queryset=TenantGroup.objects.all(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "data-tom-select": ""}),
        label=_("Tenant Group"),
    )

    class Meta:
        model = Supplier
        fields = [
            "name",
            "slug",
            "website",
            "portal_url",
            "account_id",
            "address",
            "notes",
            "tenant",
            "tenant_group",
            "is_active",
            "tags",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "slug": forms.TextInput(attrs={"class": "form-control", "slugify": "name"}),
            "website": forms.URLInput(attrs={"class": "form-control"}),
            "portal_url": forms.URLInput(attrs={"class": "form-control"}),
            "account_id": forms.TextInput(attrs={"class": "form-control"}),
            "address": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "tags": forms.SelectMultiple(attrs={"class": "form-select", "data-tom-select": ""}),
        }

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("tenant") and cleaned_data.get("tenant_group"):
            raise forms.ValidationError(_("A supplier may be scoped to a Tenant or a Tenant Group, but not both."))
        return cleaned_data

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        scope_tenant_field(self, autoset_when_single=False)
        scope_tenant_group_field(self)
        self.fields["tenant"].required = False
        cancel_url = reverse("assets:supplier_list")
        self.helper.layout = Layout(
            Fieldset(
                _("Identity"),
                Div(
                    Div("name", css_class="col-md-6"),
                    Div("slug", css_class="col-md-6"),
                    css_class="row",
                ),
            ),
            Fieldset(
                _("Commercial"),
                Div(
                    Div("website", css_class="col-md-4"),
                    Div("portal_url", css_class="col-md-4"),
                    Div("account_id", css_class="col-md-4"),
                    css_class="row",
                ),
                "address",
                "notes",
            ),
            Fieldset(
                _("Scope"),
                Div(
                    Div("tenant", css_class="col-md-6"),
                    Div("tenant_group", css_class="col-md-6"),
                    css_class="row",
                ),
            ),
            "is_active",
            "tags",
            *self.action_buttons(cancel_url),
        )
        self.append_custom_fields_to_layout()
