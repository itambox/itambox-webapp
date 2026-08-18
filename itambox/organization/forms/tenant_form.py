from crispy_forms.helper import FormHelper
from crispy_forms.layout import Div, Layout
from django import forms
from django.utils.translation import gettext_lazy as _

from core.forms import FilterForm, scope_tenant_group_field
from extras.models import Tag
from itambox.middleware import get_current_user

from ..filters import TenantFilterSet
from ..models import Tenant, TenantGroup
from .helpers import add_standard_buttons

# Codes the `money` template filter renders with a proper symbol/placement;
# anything else falls back to an ISO-code suffix.
CURRENCY_CHOICES = [
    ("EUR", _("EUR — Euro (€)")),
    ("USD", _("USD — US Dollar ($)")),
    ("GBP", _("GBP — British Pound (£)")),
    ("CHF", _("CHF — Swiss Franc")),
    ("SEK", _("SEK — Swedish Krona")),
    ("NOK", _("NOK — Norwegian Krone")),
    ("DKK", _("DKK — Danish Krone")),
    ("CAD", _("CAD — Canadian Dollar")),
    ("AUD", _("AUD — Australian Dollar")),
    ("JPY", _("JPY — Japanese Yen (¥)")),
]


class TenantForm(forms.ModelForm):
    group = forms.ModelChoiceField(
        queryset=TenantGroup.objects.all(), required=False, widget=forms.Select(attrs={"class": "form-select"})
    )
    currency = forms.ChoiceField(
        choices=CURRENCY_CHOICES,
        initial="EUR",
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text=_("ISO 4217 code used when displaying this tenant's monetary values (display only, no conversion)."),
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-select", "data-tom-select": ""}),
    )

    class Meta:
        model = Tenant
        fields = [
            "name",
            "slug",
            "group",
            "managed_by",
            "is_provider",
            "currency",
            "default_depreciation",
            "description",
            "comments",
            "tags",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "slug": forms.TextInput(attrs={"class": "form-control"}),
            "managed_by": forms.Select(attrs={"class": "form-select"}),
            "is_provider": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "default_depreciation": forms.Select(attrs={"class": "form-select", "data-tom-select": ""}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "comments": forms.Textarea(attrs={"class": "form-control", "rows": 5}),
        }
        help_texts = {
            "slug": _("URL-friendly identifier."),
        }

    def _apply_managed_by_param(self, *, managed_by_param, is_superuser, is_new):
        if not is_new or managed_by_param is None:
            return
        try:
            managed_by_id = int(managed_by_param)
        except (TypeError, ValueError):
            managed_by_id = None
        managed_by_field = self.fields.get("managed_by")
        if managed_by_field is None:
            return
        allowed_ids = set(managed_by_field.queryset.values_list("pk", flat=True))
        if managed_by_id in allowed_ids:
            managed_by_field.initial = managed_by_id
        elif not is_superuser and self.is_bound:
            # An invalid query value must not turn into an accidental
            # standalone create when the field is otherwise optional.
            self.invalid_managed_by_param = True
            self.add_error("managed_by", _("Select an authorized managing provider."))

    def __init__(self, *args, **kwargs):
        # Views may pass the actor explicitly; fall back to the request contextvar
        # so plain generic views need no wiring.
        requesting_user = kwargs.pop("user", None) or get_current_user()
        # The Managed Tenants tab passes this only as a convenience initial value.
        # It is never used to widen the field queryset or to force persistence.
        managed_by_param = kwargs.pop("managed_by_param", None)
        self.invalid_managed_by_param = False
        super().__init__(*args, **kwargs)
        # Preserve exotic codes set via the API: keep the saved value selectable
        # instead of silently dropping it on the next edit.
        current = getattr(self.instance, "currency", None)
        if current and current not in dict(CURRENCY_CHOICES):
            self.fields["currency"].choices = list(CURRENCY_CHOICES) + [(current, current)]

        is_superuser = bool(requesting_user and getattr(requesting_user, "is_superuser", False))
        is_new = not self.instance.pk

        if is_superuser:
            scope_tenant_group_field(self, field_name="group")
        else:
            # TenantGroup is global topology. Omitting the model field from the
            # bound form both hides it and makes tampered POST values inert;
            # ModelForm then preserves the saved value on updates.
            self.fields.pop("group", None)

        # ``managed_by`` is editable only while creating a tenant. Existing-tenant
        # topology changes remain a separate, protected operation.
        if is_superuser:
            # Unscoped base manager: the managing-tenant picker must list every
            # live root provider regardless of the active-tenant context.
            managed_by_qs = Tenant._base_manager.filter(
                is_provider=True,
                managed_by__isnull=True,
                deleted_at__isnull=True,
            ).order_by("name")
            if self.instance.pk:
                managed_by_qs = managed_by_qs.exclude(pk=self.instance.pk)
            self.fields["managed_by"].queryset = managed_by_qs
        elif is_new:
            # There is no queryset-level shortcut for this policy: the selector
            # must use the exact object-level add_tenant decision for each live,
            # root provider. Filtering by mere visibility/access would leak
            # providers that the actor cannot use for onboarding.
            candidate_providers = Tenant._base_manager.filter(
                is_provider=True,
                managed_by__isnull=True,
                deleted_at__isnull=True,
            ).order_by("name")
            eligible_provider_ids = {
                provider.pk
                for provider in candidate_providers
                if requesting_user
                and getattr(requesting_user, "is_authenticated", False)
                and requesting_user.has_perm("organization.add_tenant", obj=provider)
            }
            self.fields["managed_by"].queryset = Tenant._base_manager.filter(
                pk__in=eligible_provider_ids,
                is_provider=True,
                managed_by__isnull=True,
                deleted_at__isnull=True,
            ).order_by("name")
            # A user with provider onboarding authority must make the topology
            # explicit. Actors without such a provider context may still create
            # standalone/root tenants.
            self.fields["managed_by"].required = bool(eligible_provider_ids)
        else:
            self.fields.pop("managed_by", None)

        self._apply_managed_by_param(
            managed_by_param=managed_by_param,
            is_superuser=is_superuser,
            is_new=is_new,
        )

        if not is_superuser:
            # Ordinary users must never edit the protected provider-topology flag.
            self.fields.pop("is_provider", None)

        self.helper = FormHelper(self)
        self.helper.form_method = "post"
        self.helper.form_tag = True
        layout_rows = [
            Div(Div("name", css_class="col-md-6"), Div("slug", css_class="col-md-6"), css_class="row"),
        ]
        if is_superuser:
            layout_rows.append(
                Div(
                    Div("group", css_class="col-md-6"),
                    Div("currency", css_class="col-md-3"),
                    Div("default_depreciation", css_class="col-md-3"),
                    css_class="row",
                )
            )
            layout_rows.append(
                Div(
                    Div("managed_by", css_class="col-md-6"),
                    Div("is_provider", css_class="col-md-6 d-flex align-items-center"),
                    css_class="row",
                )
            )
        else:
            layout_rows.append(
                Div(
                    Div("currency", css_class="col-md-6"),
                    Div("default_depreciation", css_class="col-md-6"),
                    css_class="row",
                )
            )
            if is_new:
                layout_rows.append(Div(Div("managed_by", css_class="col-md-6"), css_class="row"))
        layout_rows.extend(["description", "comments", "tags"])
        self.helper.layout = Layout(*layout_rows)

        add_standard_buttons(self.helper, self.instance, "organization:tenant_list")


class TenantFilterForm(FilterForm):
    filterset_class = TenantFilterSet
