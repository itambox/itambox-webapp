from crispy_forms.helper import FormHelper
from crispy_forms.layout import HTML, Div, Fieldset, Layout
from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from assets.customfields import resolve_asset_custom_fields
from core.forms import CrispyFormMixin, scope_tenant_field
from core.mixins import suppress_custom_field_data_validation
from extras.customfields import (
    build_custom_field_clear_form_field,
    build_custom_field_form_field,
    clean_custom_field_form_values,
    custom_field_clear_key,
)
from organization.models import CostCenter, Location, Tenant
from procurement.models import PurchaseOrderLine

from ..models import Asset, AssetRole, AssetTagSequence, AssetType, StatusLabel, Warranty
from ..services.specifications.commands import update_asset_specifications
from ..services.specifications.contracts import DestinationAssetTypeSelectionDTO
from ..specification_adapters import (
    actor_context_for_user,
    authorization_for_asset,
    current_specification_plan,
    native_persistence_fields,
    require_command_success,
    specification_patch,
)
from ..models.choices import WarrantyTypeChoices
from .fields import StatusModelChoiceField

HTML5_DATE_FORMAT = "%Y-%m-%d"

# Changing the asset type or the owning tenant re-derives the tag preview, the
# tenant-scoped FK choices and the dynamic custom fields, so both fields swap the
# whole form back in. The two widgets must carry the identical attribute set.
HTMX_RELOAD_ATTRS = {
    "hx-post": "",
    "hx-trigger": "change",
    "hx-target": "closest form",
    "hx-swap": "outerHTML",
    "hx-vals": '{"_reload": "1"}',
    "hx-include": "closest form",
}

# Quick-add buttons live inside the field label. The markup is a literal
# developer-controlled format string; every translated or dynamic value is
# passed to format_html() as an argument so it is escaped.
QUICK_ADD_LABEL_FORMAT = (
    '{} <button type="button" class="btn btn-link p-0 ms-1 align-baseline border-0 bg-transparent'
    ' text-primary quick-add-icon" title="{}" hx-get="{}"'
    ' hx-target="#modal-placeholder"><i class="mdi mdi-plus-circle-outline"></i></button>'
)


class AssetForm(CrispyFormMixin, forms.ModelForm):
    asset_type = forms.ModelChoiceField(
        queryset=AssetType.objects.select_related("manufacturer").all(),
        label=_("Asset Type"),
        required=True,
        widget=forms.Select(attrs={"class": "form-select", "data-tom-select": "", **HTMX_RELOAD_ATTRS}),
    )
    asset_role = forms.ModelChoiceField(
        queryset=AssetRole.objects.all(),
        label=_("Asset Role"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "data-tom-select": ""}),
    )
    status = StatusModelChoiceField(
        queryset=StatusLabel.objects.all(),
        label=_("Status"),
        required=True,
        widget=forms.Select(attrs={"class": "form-select", "data-tom-select": ""}),
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.all(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "data-tom-select": ""}),
    )
    purchase_date = forms.DateField(
        widget=forms.DateInput(format=HTML5_DATE_FORMAT, attrs={"type": "date", "class": "form-control"}),
        required=False,
    )
    requestable = forms.ChoiceField(
        choices=[
            ("", _("Inherit from Asset Type (default)")),
            ("true", _("Yes, force requestable")),
            ("false", _("No, force not requestable")),
        ],
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        label=_("Requestable Status"),
    )

    # Optional inline warranty (non-model fields). When the dates are filled in,
    # the view creates a Warranty for this asset via create_inline_warranty().
    warranty_provider = forms.CharField(
        label=_("Warranty Provider"), required=False, widget=forms.TextInput(attrs={"class": "form-control"})
    )
    warranty_type = forms.ChoiceField(
        label=_("Warranty Type"),
        required=False,
        choices=[("", _("Select warranty type"))] + list(WarrantyTypeChoices.choices),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    warranty_start_date = forms.DateField(
        label=_("Warranty Start Date"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    warranty_end_date = forms.DateField(
        label=_("Warranty End Date"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    warranty_cost = forms.DecimalField(
        label=_("Warranty Cost"),
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )

    class Meta:
        model = Asset
        fields = [
            "name",
            "asset_tag",
            "serial_number",
            "asset_type",
            "asset_role",
            "status",
            "location",
            "tenant",
            "purchase_date",
            "purchase_cost",
            "salvage_value",
            "currency",
            "order_number",
            "supplier",
            "purchase_order_line",
            "cost_center",
            "in_service_date",
            "depreciation_override",
            "notes",
            "tags",
            "requestable",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "asset_tag": forms.TextInput(
                attrs={"class": "form-control", "placeholder": _("Leave blank to generate automatically")}
            ),
            "serial_number": forms.TextInput(attrs={"class": "form-control"}),
            "purchase_cost": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "salvage_value": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "currency": forms.Select(attrs={"class": "form-select", "data-tom-select": ""}),
            "order_number": forms.TextInput(attrs={"class": "form-control"}),
            "in_service_date": forms.DateInput(
                format=HTML5_DATE_FORMAT,
                attrs={"class": "form-control", "type": "date"},
            ),
            "depreciation_override": forms.Select(attrs={"class": "form-select", "data-tom-select": ""}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "tenant": forms.Select(attrs={"class": "form-select", "data-tom-select": ""}),
            "tags": forms.SelectMultiple(attrs={"class": "form-select", "data-tom-select": ""}),
            "supplier": forms.Select(attrs={"class": "form-select", "data-tom-select": ""}),
            "purchase_order_line": forms.Select(attrs={"class": "form-select", "data-tom-select": ""}),
            "cost_center": forms.Select(attrs={"class": "form-select", "data-tom-select": ""}),
        }
        help_texts = {
            "depreciation_override": _(
                "Override the depreciation policy. Leave empty to use the tenant default or asset-type schedule."
            ),
        }

    def _post_clean(self):
        with suppress_custom_field_data_validation(self.instance):
            super()._post_clean()

    def clean_status(self):
        status = self.cleaned_data.get("status")
        if isinstance(status, str):
            from django.db.models import Q

            status_obj = StatusLabel.objects.filter(Q(slug=status) | Q(name__iexact=status)).first()
            if status_obj:
                return status_obj
            raise forms.ValidationError(_("Invalid status label: %(status)s") % {"status": status})
        return status

    def clean_requestable(self):
        val = self.cleaned_data.get("requestable")
        if val == "true":
            return True
        elif val == "false":
            return False
        return None

    def clean(self):
        cleaned_data = super().clean()
        warranty_fields = (
            "warranty_provider",
            "warranty_type",
            "warranty_start_date",
            "warranty_end_date",
            "warranty_cost",
        )
        any_filled = any(cleaned_data.get(f) for f in warranty_fields)
        if any_filled:
            start = cleaned_data.get("warranty_start_date")
            end = cleaned_data.get("warranty_end_date")
            if not start:
                self.add_error(
                    "warranty_start_date",
                    _("Start date is required when adding a warranty."),
                )
            if not end:
                self.add_error(
                    "warranty_end_date",
                    _("End date is required when adding a warranty."),
                )
            if start and end and end < start:
                self.add_error(
                    "warranty_end_date",
                    _("End date cannot be before the start date."),
                )
        return clean_custom_field_form_values(
            self,
            cleaned_data,
            self.custom_field_definitions,
            self.custom_field_clear_keys,
        )

    def create_inline_warranty(self, asset):
        cd = self.cleaned_data
        start = cd.get("warranty_start_date")
        end = cd.get("warranty_end_date")
        if not (start and end):
            return None

        kwargs = dict(
            asset=asset,
            start_date=start,
            end_date=end,
            provider=cd.get("warranty_provider") or "",
            cost=cd.get("warranty_cost"),
        )
        wt = cd.get("warranty_type")
        if wt:
            kwargs["warranty_type"] = wt
        return Warranty.objects.create(**kwargs)

    def __init__(self, *args, **kwargs):
        request = kwargs.pop("request", None)
        self.request = request
        explicit_initial = kwargs.get("initial") or {}
        super().__init__(*args, **kwargs)
        scope_tenant_field(self)
        self.fields["cost_center"].label_from_instance = lambda cost_center: (
            f"{cost_center.code}: {cost_center.name}" if cost_center.code else cost_center.name
        )
        self.helper = FormHelper(self)
        self.helper.form_method = "post"
        self.helper.form_tag = True

        self._configure_requestable_initial()
        self._configure_required_fields()
        self._configure_quick_add_labels()
        self._configure_htmx_reload_widgets()

        asset_type_id = self._resolve_asset_type_id(request, explicit_initial)
        selected_asset_type = self._resolve_asset_type(asset_type_id)
        selected_tenant = self._resolve_selected_tenant()

        self._configure_tenant_scoped_querysets(selected_tenant)
        self._configure_asset_tag_sequence_help_text(selected_tenant, selected_asset_type)
        self._configure_default_asset_role(selected_asset_type)
        # Custom fields must exist before the layout pairs them into rows.
        self._configure_custom_fields(selected_asset_type)

        self.helper.layout = self._build_layout(reverse("assets:asset_list"))

    def _resolve_asset_type_id(self, request, explicit_initial):
        """Return the raw asset-type ID the form should work from, or None.

        Precedence (#81): a bound ``asset_type`` key wins outright — an empty or
        malformed submitted value must *not* fall back to a lower-precedence
        source, form validation reports it instead. Otherwise the quick-add
        ``?asset_type=`` query value, then explicit ``initial``, then the
        instance. Never mutates ``data`` / ``initial`` / ``instance``.
        """
        if self.is_bound and "asset_type" in self.data:
            try:
                return int(self.data.get("asset_type"))
            except (ValueError, TypeError):
                return None

        if request and request.GET.get("asset_type"):
            try:
                return int(request.GET.get("asset_type"))
            except (ValueError, TypeError):
                return None

        if "asset_type" in explicit_initial:
            asset_type_val = explicit_initial.get("asset_type")
            if isinstance(asset_type_val, AssetType):
                return asset_type_val.pk
            return asset_type_val

        return self.instance.asset_type_id

    def _resolve_asset_type(self, asset_type_id):
        """Resolve the one AssetType driving preview, role default and custom fields."""
        if not asset_type_id:
            return None
        try:
            return AssetType.objects.get(pk=asset_type_id)
        except AssetType.DoesNotExist:
            return None

    def _resolve_selected_tenant(self):
        """Resolve the tenant the form is scoped to, or None.

        A *truthy* bound value wins; an empty one falls through to ``initial``
        (which ``scope_tenant_field()`` may have autoset) and then to the
        instance. An unresolvable value simply yields no selected tenant.
        """
        raw_tenant = None
        if self.is_bound and self.data.get("tenant"):
            raw_tenant = self.data.get("tenant")
        elif self.initial.get("tenant"):
            raw_tenant = self.initial.get("tenant")
        elif self.instance and self.instance.tenant:
            raw_tenant = self.instance.tenant

        if not raw_tenant:
            return None
        if isinstance(raw_tenant, Tenant):
            return raw_tenant
        try:
            return Tenant.objects.get(pk=raw_tenant)
        except (Tenant.DoesNotExist, ValueError, TypeError):
            return None

    def _configure_requestable_initial(self):
        """Map the saved tri-state ``requestable`` onto the select's choices."""
        if not (self.instance and self.instance.pk):
            return
        if self.instance.requestable is None:
            self.initial["requestable"] = ""
        elif self.instance.requestable is True:
            self.initial["requestable"] = "true"
        else:
            self.initial["requestable"] = "false"

    def _configure_required_fields(self):
        # Ensure asset_tag is required in the form
        self.fields["asset_tag"].required = True

    def _configure_quick_add_labels(self):
        """Put quick-add buttons inside the field labels instead of layout divs."""
        quick_add_fields = (
            ("asset_type", _("Asset Type"), _("Add new Asset Type"), "assets:assettype_create"),
            ("asset_role", _("Asset Role"), _("Add new Asset Role"), "assets:assetrole_create"),
            ("location", _("Location"), _("Add new Location"), "organization:location_create"),
        )
        for field_name, label, title, url_name in quick_add_fields:
            if field_name not in self.fields:
                continue
            self.fields[field_name].label = format_html(
                QUICK_ADD_LABEL_FORMAT,
                label,
                title,
                reverse(url_name) + "?_quickadd=1",
            )

    def _configure_htmx_reload_widgets(self):
        # The tenant picker reloads the form for the same reasons asset_type does.
        if "tenant" in self.fields:
            self.fields["tenant"].widget.attrs.update(HTMX_RELOAD_ATTRS)

    def _configure_tenant_scoped_querysets(self, selected_tenant):
        """Rescope tenant-owned FK choice fields per request.

        B2: their querysets are frozen at import time (no tenant context), so they
        would otherwise expose every tenant's Locations / CostCenters /
        PurchaseOrderLines and permit cross-tenant FK assignment. Re-evaluate
        through the scoping managers so choices are limited to the active tenant,
        narrowed to the selected tenant when one is resolvable. Asset.clean()
        validates the final selection as defence-in-depth.
        """
        tenant_scoped_fk_fields = {
            "location": Location,
            "cost_center": CostCenter,
            "purchase_order_line": PurchaseOrderLine,
        }
        for fk_field_name, fk_model in tenant_scoped_fk_fields.items():
            if fk_field_name in self.fields:
                fk_qs = fk_model.objects.all()
                if selected_tenant:
                    fk_qs = fk_qs.filter(tenant=selected_tenant)
                self.fields[fk_field_name].queryset = fk_qs

    def _configure_asset_tag_sequence_help_text(self, selected_tenant, selected_asset_type):
        """Preview the next tag for the selected scope in the asset_tag help text.

        Resolving does not consume a tag, but it does create the global default
        sequence when nothing else matches — a deliberately preserved side effect.
        """
        preview_asset = Asset(tenant=selected_tenant, asset_type=selected_asset_type)
        sequence = AssetTagSequence.resolve_sequence_for_asset(preview_asset)
        if sequence:
            suggested_tag = sequence.next_tag_preview
            help_text = format_html(
                '<span class="text-muted small">{} <a href="#" class="text-primary font-monospace"'
                ' data-fill-target="id_asset_tag" data-fill-value="{}">{}</a></span>',
                _("Suggested:"),
                suggested_tag,
                suggested_tag,
            )
        else:
            help_text = format_html(
                '<span class="text-muted small">{}</span>',
                _("No active tag sequence found for this scope."),
            )
        self.fields["asset_tag"].help_text = help_text

    def _configure_default_asset_role(self, selected_asset_type):
        """Seed a brand-new asset's role from its type; never overwrite a set one."""
        if self.instance.pk or selected_asset_type is None:
            return

        current_role = None
        if self.data and "asset_role" in self.data:
            current_role = self.data.get("asset_role")
        elif self.initial and "asset_role" in self.initial:
            current_role = self.initial.get("asset_role")

        if not current_role and selected_asset_type.asset_role:
            self.fields["asset_role"].initial = selected_asset_type.asset_role

    def _configure_custom_fields(self, selected_asset_type):
        """Attach the dynamic ``cf_*`` fields and record their layout order."""
        stored_values = {}
        if self.instance and self.instance.pk and self.instance.custom_field_data:
            stored_values = self.instance.custom_field_data

        self.custom_field_keys = []
        self.custom_field_definitions = {}
        self.custom_field_clear_keys = {}
        resolved_fields = resolve_asset_custom_fields(selected_asset_type, stored_values)
        for resolved in resolved_fields:
            field = resolved.definition
            field_key = f"cf_{field.name}"
            self.custom_field_keys.append(field_key)
            self.custom_field_definitions[field_key] = field
            form_field = build_custom_field_form_field(
                field,
                stored_values.get(field.name),
                read_only=resolved.read_only,
            )
            if form_field:
                self.fields[field_key] = form_field
                if not form_field.disabled:
                    clear_key = custom_field_clear_key(field.name)
                    self.fields[clear_key] = build_custom_field_clear_form_field()
                    self.custom_field_clear_keys[field_key] = clear_key

    def _build_layout(self, cancel_url):
        # Grouped, standardized section order: Identity -> Classification ->
        # Assignment -> Procurement & Financial -> Lifecycle -> Custom -> Notes.
        layout_elements = [
            Fieldset(
                _("Identity"),
                Div(Div("name", css_class="col-md-6"), Div("asset_tag", css_class="col-md-6"), css_class="row"),
                Div(Div("serial_number", css_class="col-md-6"), Div("status", css_class="col-md-6"), css_class="row"),
            ),
            Fieldset(
                _("Classification"),
                Div(Div("asset_type", css_class="col-md-6"), Div("asset_role", css_class="col-md-6"), css_class="row"),
            ),
            Fieldset(
                _("Assignment"),
                Div(Div("location", css_class="col-md-6"), Div("tenant", css_class="col-md-6"), css_class="row"),
            ),
            Fieldset(
                _("Procurement & Financial"),
                Div(
                    Div("purchase_date", css_class="col-md-4"),
                    Div("order_number", css_class="col-md-4"),
                    Div("supplier", css_class="col-md-4"),
                    css_class="row",
                ),
                Div(Div("purchase_order_line", css_class="col-md-6"), css_class="row"),
                Div(
                    Div("purchase_cost", css_class="col-md-4"),
                    Div("currency", css_class="col-md-4"),
                    Div("salvage_value", css_class="col-md-4"),
                    css_class="row",
                ),
                Div(Div("cost_center", css_class="col-md-6"), css_class="row"),
            ),
            Fieldset(
                _("Lifecycle"),
                Div(
                    Div("in_service_date", css_class="col-md-6"),
                    Div("depreciation_override", css_class="col-md-6"),
                    css_class="row",
                ),
            ),
        ]

        if self.custom_field_keys:
            cf_divs = []
            for i in range(0, len(self.custom_field_keys), 2):
                chunk = self.custom_field_keys[i : i + 2]
                row_cols = []
                for key in chunk:
                    clear_key = self.custom_field_clear_keys.get(key)
                    fields = [key]
                    if clear_key:
                        fields.append(clear_key)
                    row_cols.append(Div(*fields, css_class="col-md-6"))
                cf_divs.append(Div(*row_cols, css_class="row"))
            layout_elements.append(Fieldset(_("Custom Specifications"), *cf_divs, css_class="mb-4 border p-3 rounded"))

        layout_elements.append(
            Fieldset(
                _("Optional: Warranty"),
                HTML(
                    format_html(
                        '<p class="text-muted small">{}</p>',
                        _("Fill in to create a warranty for this asset; leave blank to skip."),
                    )
                ),
                Div(
                    Div("warranty_provider", css_class="col-md-6"),
                    Div("warranty_type", css_class="col-md-6"),
                    css_class="row",
                ),
                Div(
                    Div("warranty_start_date", css_class="col-md-4"),
                    Div("warranty_end_date", css_class="col-md-4"),
                    Div("warranty_cost", css_class="col-md-4"),
                    css_class="row",
                ),
            )
        )

        layout_elements.append(
            Fieldset(
                _("Notes & Tags"),
                Div(Div("tags", css_class="col-md-6"), Div("requestable", css_class="col-md-6"), css_class="row"),
                "notes",
            )
        )

        layout_elements.extend(self.action_buttons(cancel_url))

        return Layout(*layout_elements)

    def save(self, commit=True):
        # ModelForm.save(commit=False) is intentionally used without the old
        # custom-field merge.  The command below is the only value/type-switch
        # writer; native fields are saved separately so a stale form instance
        # cannot overwrite command-owned state.
        from django.forms import ModelForm

        # is_valid() has already copied the submitted Type onto instance.
        # Deferred native persistence must retain the stored Type, not that
        # mutated in-memory value; only save_m2m may execute the switch.
        previous_type_id = None
        if not commit and self.instance.pk:
            previous_type_id = Asset._base_manager.values_list("asset_type_id", flat=True).get(pk=self.instance.pk)
        instance = ModelForm.save(self, commit=False)
        self._native_save_m2m = getattr(self, "save_m2m", None)
        self._pending_target_asset_type_id = instance.asset_type_id
        self._pending_create = not bool(instance.pk)
        if not commit:
            # A caller following Django's commit=False lifecycle must not be
            # able to persist a type switch through instance.save().  The
            # pending command runs from save_m2m after the caller has saved
            # native fields.
            instance.asset_type_id = previous_type_id
            self.save_m2m = self._save_pending_m2m
            return instance

        actor = actor_context_for_user(getattr(self.request, "user", None))
        with transaction.atomic():
            if instance.pk:
                current = Asset._base_manager.get(pk=instance.pk)
                self._apply_specification_command(current, actor)
                current = self._persist_native_update(instance)
            else:
                current = self._persist_native_create(instance)
                self._apply_specification_command(current, actor)
            self.instance = Asset._base_manager.get(pk=current.pk)
            if self._native_save_m2m is not None:
                self._native_save_m2m()
        return self.instance

    def _specification_patch(self):
        return specification_patch(
            definitions=self.custom_field_definitions,
            cleaned_values=self.cleaned_data,
            fields=self.fields,
            clear_keys=self.custom_field_clear_keys,
        )

    def _native_field_names(self):
        concrete_names = {field.name for field in Asset._meta.concrete_fields}
        return tuple(
            name
            for name in self.changed_data
            if name in concrete_names and name not in {"asset_type", "custom_field_data"}
        )

    def _persist_native_update(self, instance):
        current = Asset._base_manager.get(pk=instance.pk)
        field_names = self._native_field_names()
        for field_name in field_names:
            field = Asset._meta.get_field(field_name)
            setattr(current, field.attname, getattr(instance, field.attname))
        if field_names:
            current.save(update_fields=native_persistence_fields(current, field_names))
        return current

    def _persist_native_create(self, instance):
        # The empty specification object is not a user value.  Suppress only
        # dynamic custom-field validation for this initial native row; the
        # canonical command immediately validates and writes the requested
        # patch/type destination inside the same outer transaction.
        from core.mixins import suppress_custom_field_data_validation

        instance.custom_field_data = {}
        with suppress_custom_field_data_validation(instance):
            instance.save()
        return instance

    def _apply_specification_command(self, current, actor):
        target_type_id = self._pending_target_asset_type_id
        patch = self._specification_patch()
        if target_type_id == current.asset_type_id and not patch.set_values and not patch.clear_keys:
            return
        if target_type_id is None:
            raise ValidationError("An Asset Type is required for specification changes.")
        authorization = authorization_for_asset(
            user=getattr(self.request, "user", None),
            tenant_id=current.tenant_id,
        )
        plan = current_specification_plan(current, target_kind="asset", asset_type_id=target_type_id)
        result = update_asset_specifications(
            authorization=authorization,
            asset_id=current.pk,
            destination=DestinationAssetTypeSelectionDTO(
                presence="replace",
                asset_type_id=int(target_type_id),
            ),
            expected_resource_revision=plan.resource_revision,
            expected_definition_revision=plan.definition_revision,
            patch=patch,
        )
        require_command_success(result)

    def _save_pending_m2m(self):
        with transaction.atomic():
            if self._native_save_m2m is not None:
                self._native_save_m2m()
            if not self.instance.pk:
                return
            current = Asset._base_manager.get(pk=self.instance.pk)
            actor = actor_context_for_user(getattr(self.request, "user", None))
            self._apply_specification_command(current, actor)
            self.instance = Asset._base_manager.get(pk=current.pk)
