from crispy_forms.helper import FormHelper
from crispy_forms.layout import HTML, Column, Fieldset, Layout, Row, Submit
from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from assets.customfields import resolve_effective_custom_fields
from core.forms import SlugModelForm
from extras.customfields import CustomFieldModelFormMixin
from extras.models import CustomFieldset, Tag

from ..models import AssetRole, AssetType, Category, Manufacturer
from ..services.specifications.commands import (
    apply_category_defaults,
    create_asset_type,
    preview_apply_category_defaults,
    preview_asset_type_create,
    set_asset_type_composition,
    update_asset_type_specifications,
)
from ..services.specifications.contracts import ExplicitFieldsetSelectionDTO
from ..specification_adapters import (
    actor_context_for_user,
    create_fieldset_selection,
    current_specification_plan,
    native_persistence_fields,
    discard_staged_image,
    native_asset_type_create_input,
    owner_id_from_result,
    require_command_success,
    specification_patch,
    stage_uploaded_image,
)


class AssetTypeForm(CustomFieldModelFormMixin, SlugModelForm):
    manufacturer = forms.ModelChoiceField(
        queryset=Manufacturer.objects.all(), widget=forms.Select(attrs={"class": "form-select"})
    )
    asset_role = forms.ModelChoiceField(
        queryset=AssetRole.objects.all(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        label=_("Asset Role"),
    )
    custom_fieldsets = forms.ModelMultipleChoiceField(
        queryset=CustomFieldset.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-select", "data-tom-select": ""}),
        label=_("Specification fieldsets"),
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-select", "data-tomselect-tags": "true"}),
        label=_("Tags"),
    )

    class Meta:
        model = AssetType
        fields = [
            "manufacturer",
            "part_number",
            "ean",
            "model",
            "slug",
            "eol_months",
            "category",
            "asset_role",
            "custom_fieldsets",
            "depreciation",
            "image",
            "description",
            "comments",
            "tags",
            "requestable",
        ]
        widgets = {
            "model": forms.TextInput(attrs={"class": "form-control"}),
            "slug": forms.TextInput(attrs={"class": "form-control", "slugify": "model"}),
            "part_number": forms.TextInput(attrs={"class": "form-control"}),
            "ean": forms.TextInput(attrs={"class": "form-control", "inputmode": "numeric"}),
            "eol_months": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "category": forms.Select(attrs={"class": "form-select"}),
            "depreciation": forms.Select(attrs={"class": "form-select"}),
            "image": forms.FileInput(attrs={"class": "form-control", "style": "max-width: 400px;"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "comments": forms.Textarea(attrs={"class": "form-control", "rows": 5}),
        }
        help_texts = {
            "slug": _("URL-friendly identifier. Leave blank to auto-generate."),
            "ean": _("Barcode (EAN, UPC, or GTIN). Scan this barcode to view assets of this type."),
        }

    def _raw_selected_fieldset_ids(self):
        if self.is_bound:
            if hasattr(self.data, "getlist"):
                values = self.data.getlist("custom_fieldsets")
            else:
                values = self.data.get("custom_fieldsets", [])
                if not isinstance(values, (list, tuple)):
                    values = [values]
            return [int(value) for value in values if str(value).isdigit()]
        if self.instance and self.instance.pk:
            return list(self.instance.fieldset_memberships.order_by("position").values_list("fieldset_id", flat=True))
        if not self._custom_fieldsets_explicit:
            category_id = getattr(self._draft_category, "pk", self._draft_category)
            if category_id:
                return list(
                    Category.objects.filter(pk=category_id, default_fieldset_memberships__isnull=False)
                    .values_list("default_fieldset_memberships__fieldset_id", flat=True)
                    .order_by("default_fieldset_memberships__position")
                )
        initial = self.initial.get("custom_fieldsets", [])
        if hasattr(initial, "values_list"):
            return list(initial.values_list("pk", flat=True))
        return [getattr(value, "pk", value) for value in initial]

    def _selected_fieldsets(self):
        ids = self._raw_selected_fieldset_ids()
        by_id = (
            CustomFieldset.objects.filter(pk__in=ids)
            .prefetch_related(
                "field_memberships__custom_field__object_types",
                "field_memberships__custom_field__choice_set__choices",
            )
            .in_bulk(ids)
        )
        return [by_id[fieldset_id] for fieldset_id in ids if fieldset_id in by_id]

    def clean_custom_fieldsets(self):
        fieldsets = self.cleaned_data["custom_fieldsets"]
        raw_ids = self._raw_selected_fieldset_ids()
        if len(raw_ids) != len(set(raw_ids)):
            raise forms.ValidationError(_("Each specification fieldset may only be selected once."))
        return fieldsets

    def get_custom_field_definitions(self):
        stored = dict(self.instance.custom_field_data or {}) if self.instance and self.instance.pk else {}
        return resolve_effective_custom_fields(
            self._selected_fieldsets(),
            "assettype",
            stored,
        )

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        supplied_initial = kwargs.get("initial") or {}
        self._custom_fieldsets_explicit = "custom_fieldsets" in supplied_initial
        self._draft_category = supplied_initial.get("category")
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_method = "post"
        self.helper.form_tag = True
        self.fields["slug"].widget.attrs["slugify"] = "model"
        self.fields["custom_fieldsets"].initial = self._raw_selected_fieldset_ids()

        button_text = _("Update") if self.instance.pk else _("Create")
        cancel_url = self.instance.get_absolute_url() if self.instance.pk else reverse("assets:assettype_list")
        layout_elements = [
            Fieldset(
                _("General Information"),
                Row(Column("manufacturer", css_class="col-md-6"), Column("model", css_class="col-md-6")),
                Row(
                    Column("part_number", css_class="col-md-3"),
                    Column("ean", css_class="col-md-3"),
                    Column("slug", css_class="col-md-3"),
                    Column("eol_months", css_class="col-md-3"),
                ),
                Row(Column("image", css_class="col-md-6"), Column("description", css_class="col-md-6")),
            ),
            Fieldset(
                _("Classification & Financial"),
                Row(
                    Column("category", css_class="col-md-3"),
                    Column("asset_role", css_class="col-md-3"),
                    Column("custom_fieldsets", css_class="col-md-3"),
                    Column("depreciation", css_class="col-md-3"),
                ),
                Row(Column("requestable", css_class="col-md-4 mt-4")),
            ),
        ]
        if self.custom_field_keys:
            rows = []
            for index in range(0, len(self.custom_field_keys), 2):
                columns = []
                for key in self.custom_field_keys[index : index + 2]:
                    fields = [key]
                    clear_key = self.custom_field_clear_keys.get(key)
                    if clear_key:
                        fields.append(clear_key)
                    columns.append(Column(*fields, css_class="col-md-6"))
                rows.append(Row(*columns))
            layout_elements.append(Fieldset(_("Specifications"), *rows))
        else:
            layout_elements.append(
                Fieldset(
                    _("Specifications"),
                    HTML(
                        '<div class="alert alert-info d-flex align-items-center mb-0" role="alert">'
                        '  <i class="mdi mdi-information-outline me-2"></i>'
                        "  <div>Select specification fieldsets to add specifications.</div>"
                        "</div>"
                    ),
                )
            )
        layout_elements.extend(
            [
                Fieldset(_("Additional Information"), "comments", Row(Column("tags", css_class="col-md-8"))),
                HTML('<div class="mt-3">'),
                Submit("submit", button_text, css_class="btn btn-primary"),
                HTML(format_html('<a href="{}" class="btn btn-outline-secondary ms-2">Cancel</a>', cancel_url)),
                HTML("</div>"),
            ]
        )
        self.helper.layout = Layout(*layout_elements)

    def _actor(self):
        user = getattr(self.request, "user", None)
        return actor_context_for_user(user)

    def _ordered_selected_fieldsets(self):
        selected = self.cleaned_data.get("custom_fieldsets")
        if selected is None:
            return ()
        selected_by_id = {fieldset.pk: fieldset for fieldset in selected}
        return tuple(
            selected_by_id[fieldset_id]
            for fieldset_id in self._raw_selected_fieldset_ids()
            if fieldset_id in selected_by_id
        )

    def _create_selection(self):
        # A missing key is the form-level representation of omission.  An
        # explicitly empty multi-select is a deliberate empty composition.
        omitted = self.is_bound and "custom_fieldsets" not in self.data
        return create_fieldset_selection(self._ordered_selected_fieldsets(), omitted=omitted)

    def _patch(self):
        return specification_patch(
            definitions=self.custom_field_definitions,
            cleaned_values=self.cleaned_data,
            fields=self.fields,
            clear_keys=self.custom_field_clear_keys,
        )

    def _native_values(self, instance):
        return {
            "manufacturer": instance.manufacturer,
            "model": instance.model,
            "slug": instance.slug,
            "part_number": instance.part_number,
            "ean": instance.ean,
            "region": instance.region,
            "configuration": instance.configuration,
            "eol_months": instance.eol_months,
            "category": instance.category,
            "asset_role": instance.asset_role,
            "depreciation": instance.depreciation,
            "description": instance.description,
            "comments": instance.comments,
            "requestable": instance.requestable,
            "tags": self.cleaned_data.get("tags", ()),
        }

    def _native_field_names(self):
        concrete_names = {field.name for field in AssetType._meta.concrete_fields}
        return tuple(name for name in self.changed_data if name in concrete_names)

    def _persist_native_update(self, instance):
        current = AssetType.all_objects.get(pk=instance.pk)
        field_names = self._native_field_names()
        for field_name in field_names:
            field = AssetType._meta.get_field(field_name)
            setattr(current, field.attname, getattr(instance, field.attname))
        if field_names:
            current.save(update_fields=native_persistence_fields(current, field_names))
        return current

    def _command_update(self, instance, actor):
        plan = current_specification_plan(instance, target_kind="asset_type")
        selection = self._create_selection()
        if selection.presence == "omitted":
            result = update_asset_type_specifications(
                actor=actor,
                asset_type_id=instance.pk,
                expected_resource_revision=plan.resource_revision,
                expected_definition_revision=plan.definition_revision,
                patch=self._patch(),
            )
        else:
            result = set_asset_type_composition(
                actor=actor,
                asset_type_id=instance.pk,
                fieldsets=ExplicitFieldsetSelectionDTO(identities=selection.identities),
                expected_resource_revision=plan.resource_revision,
                expected_definition_revision=plan.definition_revision,
                patch=self._patch(),
            )
        require_command_success(result)

    def _command_create(self, instance, actor):
        stage_id = None
        uploaded = instance.image
        if uploaded and getattr(uploaded, "_file", None) is not None:
            stage_id = stage_uploaded_image(actor=actor, uploaded=uploaded)
        try:
            native = native_asset_type_create_input(self._native_values(instance), staged_image_id=stage_id)
            selection = self._create_selection()
            patch = self._patch()
            preview = require_command_success(
                preview_asset_type_create(
                    actor=actor,
                    native=native,
                    fieldsets=selection,
                    patch=patch,
                )
            )
            if getattr(preview, "issues", ()):
                raise ValidationError("; ".join(issue.message_key for issue in preview.issues))
            result = create_asset_type(
                actor=actor,
                native=native,
                fieldsets=selection,
                patch=patch,
                preview_token=preview.preview_token,
                expected_definition_revision=preview.expected_definition_revision,
                expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
            )
            owner_id = owner_id_from_result(result)
            created = AssetType.all_objects.get(pk=owner_id)
            stage_id = None  # Successful create owns the staged image lifecycle.
            return created
        finally:
            if stage_id is not None:
                discard_staged_image(stage_id=stage_id, actor=actor)

    def _save_m2m(self):
        # The ordered through-model belongs exclusively to the composition
        # command. Keep Django's native M2M handling (including tags) while
        # withholding this one command-owned field from its direct .set().
        cleaned_data = self.cleaned_data
        self.cleaned_data = {key: value for key, value in cleaned_data.items() if key != "custom_fieldsets"}
        try:
            super()._save_m2m()
        finally:
            self.cleaned_data = cleaned_data

    def _save_pending_m2m(self):
        native_save_m2m = self._native_save_m2m
        with transaction.atomic():
            if native_save_m2m is not None:
                native_save_m2m()
            if not self.instance.pk:
                return
            actor = self._actor()
            current = AssetType.all_objects.get(pk=self.instance.pk)
            if self._pending_create and self._create_selection().presence == "omitted" and current.category_id:
                plan = current_specification_plan(current, target_kind="asset_type")
                preview = require_command_success(
                    preview_apply_category_defaults(
                        actor=actor,
                        asset_type_id=current.pk,
                        expected_resource_revision=plan.resource_revision,
                        patch=self._patch(),
                    )
                )
                result = apply_category_defaults(
                    actor=actor,
                    asset_type_id=current.pk,
                    preview_token=preview.preview_token,
                    expected_resource_revision=preview.expected_resource_revision,
                    expected_definition_revision=preview.expected_definition_revision,
                    expected_category_default_snapshot_revision=preview.expected_category_default_snapshot_revision,
                    patch=self._patch(),
                )
                require_command_success(result)
            else:
                self._command_update(current, actor)
            self.instance = AssetType.all_objects.get(pk=self.instance.pk)

    def save(self, commit=True):
        # Bypass CustomFieldModelFormMixin.save: that mixin is a legacy second
        # authority which merges and writes custom_field_data directly.
        from django.forms import ModelForm

        instance = ModelForm.save(self, commit=False)
        self._native_save_m2m = getattr(self, "save_m2m", None)
        self._pending_create = not bool(instance.pk)
        if not commit:
            self.save_m2m = self._save_pending_m2m
            return instance

        actor = self._actor()
        with transaction.atomic():
            if instance.pk:
                current = AssetType.all_objects.get(pk=instance.pk)
                self._command_update(current, actor)
                current = self._persist_native_update(instance)
                self.instance = AssetType.all_objects.get(pk=current.pk)
                if self._native_save_m2m is not None:
                    self._native_save_m2m()
            else:
                self.instance = self._command_create(instance, actor)
        return self.instance
