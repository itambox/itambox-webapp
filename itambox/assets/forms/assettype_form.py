from crispy_forms.helper import FormHelper
from crispy_forms.layout import HTML, Column, Fieldset, Layout, Row, Submit
from django import forms
from django.db import transaction
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from assets.customfields import resolve_fieldsets_custom_fields
from core.forms import SlugModelForm
from extras.customfields import CustomFieldModelFormMixin
from extras.models import CustomField, CustomFieldset, Tag

from ..models import AssetRole, AssetType, AssetTypeFieldset, Category, Manufacturer


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
        by_id = CustomFieldset.objects.in_bulk(ids)
        return [by_id[fieldset_id] for fieldset_id in ids if fieldset_id in by_id]

    def clean_custom_fieldsets(self):
        fieldsets = self.cleaned_data["custom_fieldsets"]
        raw_ids = self._raw_selected_fieldset_ids()
        if len(raw_ids) != len(set(raw_ids)):
            raise forms.ValidationError(_("Each specification fieldset may only be selected once."))
        return fieldsets

    def get_custom_field_definitions(self):
        stored = dict(self.instance.custom_field_data or {}) if self.instance and self.instance.pk else {}
        return resolve_fieldsets_custom_fields(
            self._selected_fieldsets(),
            {CustomField.SCOPE_ASSET_TYPE, CustomField.SCOPE_BOTH},
            stored,
        )

    def __init__(self, *args, **kwargs):
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
                rows.append(
                    Row(*[Column(key, css_class="col-md-6") for key in self.custom_field_keys[index : index + 2]])
                )
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

    def save(self, commit=True):
        instance = super().save(commit=False)
        if not commit:
            return instance
        selected_ids = set(self.cleaned_data["custom_fieldsets"].values_list("pk", flat=True))
        ordered_ids = [fieldset_id for fieldset_id in self._raw_selected_fieldset_ids() if fieldset_id in selected_ids]
        with transaction.atomic():
            instance.save()
            instance.tags.set(self.cleaned_data.get("tags", ()))
            AssetType.objects.select_for_update().get(pk=instance.pk)
            instance.fieldset_memberships.all().delete()
            AssetTypeFieldset.objects.bulk_create(
                [
                    AssetTypeFieldset(asset_type=instance, fieldset_id=fieldset_id, position=index * 10)
                    for index, fieldset_id in enumerate(ordered_ids, start=1)
                ]
            )
        return instance
