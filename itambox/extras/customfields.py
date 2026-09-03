"""Unified custom-field form, validation, filtering, and merge plumbing."""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from crispy_forms.layout import Div, Fieldset
from django import forms
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from extras.definition_contract import validate_custom_field_regex
from extras.models import CustomField

JCS_INTEGER_MIN = -9007199254740991
JCS_INTEGER_MAX = 9007199254740991
RFC1123_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def _definition(item):
    return getattr(item, "definition", item)


def is_omitted_optional_single_select(definition, value):
    return definition.field_type == CustomField.FIELD_TYPE_SINGLE_SELECT and value == "" and not definition.required


def custom_field_clear_key(field_name):
    return f"cf_{field_name}__clear"


def build_custom_field_clear_form_field():
    return forms.BooleanField(
        required=False,
        label=_("Remove value"),
        help_text=_("Explicitly remove the stored value."),
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )


def clean_custom_field_form_values(form, cleaned_data, custom_field_definitions, clear_keys=None):
    clear_keys = clear_keys or {}
    for key, definition in custom_field_definitions.items():
        if key not in cleaned_data or form.fields[key].disabled:
            continue
        value = cleaned_data[key]
        clear_key = clear_keys.get(key)
        if clear_key and cleaned_data.get(clear_key):
            continue
        if is_omitted_optional_single_select(definition, value):
            continue
        if value is None and not definition.nullable:
            continue
        try:
            cleaned_data[key] = validate_custom_field_value(definition, value)
        except ValidationError as exc:
            form.add_error(key, exc)
    return cleaned_data


def _parse_decimal(cf, value):
    if isinstance(value, bool) or value is None:
        raise ValidationError(_("Enter a decimal value."), code="INVALID_TYPE")
    raw = str(value)
    scale = cf.decimal_scale
    if scale is None or not 0 <= scale <= 6:
        raise ValidationError(_("The decimal definition is invalid."), code="INVALID_RANGE")
    if "e" in raw.casefold() or raw.startswith("+"):
        raise ValidationError(_("Enter a base-10 decimal without an exponent."), code="INVALID_VALUE")
    grammar = r"^-?(0|[1-9][0-9]*)$" if scale == 0 else rf"^-?(0|[1-9][0-9]*)(\.[0-9]{{1,{scale}}})?$"
    if not re.fullmatch(grammar, raw):
        raise ValidationError(_("Enter a canonical decimal value."), code="INVALID_VALUE")
    if len(raw.lstrip("-").split(".", 1)[0]) > 18:
        raise ValidationError(_("The decimal value is too large."), code="INVALID_RANGE")
    try:
        decimal_value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValidationError(_("Enter a decimal value."), code="INVALID_VALUE") from exc
    if decimal_value.is_zero() and decimal_value.is_signed():
        raise ValidationError(_("Negative zero is not allowed."), code="INVALID_VALUE")
    return decimal_value


def _canonical_decimal(cf, value):
    decimal_value = _parse_decimal(cf, value)
    if cf.minimum_value is not None and decimal_value < cf.minimum_value:
        raise ValidationError(_("The value is below the allowed minimum."), code="INVALID_RANGE")
    if cf.maximum_value is not None and decimal_value > cf.maximum_value:
        raise ValidationError(_("The value is above the allowed maximum."), code="INVALID_RANGE")
    return format(decimal_value, f".{cf.decimal_scale}f")


def _validate_hostname(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 253 or value.endswith("."):
        return False
    return all(RFC1123_LABEL.fullmatch(label) for label in value.split("."))


def _validate_text(cf, value):
    if not isinstance(value, str):
        raise ValidationError(_("Enter text."), code="INVALID_TYPE")
    if cf.text_max_length is not None and len(value) > cf.text_max_length:
        raise ValidationError(_("The text is too long."), code="INVALID_RANGE")
    if cf.regex:
        validate_custom_field_regex(cf.regex)
        if re.fullmatch(cf.regex, value, flags=re.ASCII) is None:
            raise ValidationError(_("The value has an invalid format."), code="INVALID_VALUE")
    if cf.validation_rule == "rfc1123_hostname" and not _validate_hostname(value):
        raise ValidationError(_("Enter a valid hostname."), code="INVALID_VALUE")
    return value


def _validate_integer(cf, value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(_("Enter an integer."), code="INVALID_TYPE")
    if not JCS_INTEGER_MIN <= value <= JCS_INTEGER_MAX:
        raise ValidationError(_("The integer is outside the supported range."), code="INVALID_RANGE")
    if cf.minimum_value is not None and Decimal(value) < cf.minimum_value:
        raise ValidationError(_("The value is below the allowed minimum."), code="INVALID_RANGE")
    if cf.maximum_value is not None and Decimal(value) > cf.maximum_value:
        raise ValidationError(_("The value is above the allowed maximum."), code="INVALID_RANGE")
    return value


def _validate_date(cf, value):
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        raise ValidationError(_("Enter a date."), code="INVALID_TYPE")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(_("Enter an ISO date."), code="INVALID_VALUE") from exc
    if parsed.isoformat() != value:
        raise ValidationError(_("Enter an ISO date."), code="INVALID_VALUE")
    return value


def _validate_boolean(cf, value):
    if not isinstance(value, bool):
        raise ValidationError(_("Enter a boolean."), code="INVALID_TYPE")
    return value


def _active_choice_keys(cf):
    if cf.choice_set_id is None:
        raise ValidationError(_("The choice definition is invalid."), code="INVALID_CHOICE")
    return set(cf.choice_set.choices.filter(lifecycle=CustomField.LIFECYCLE_ACTIVE).values_list("key", flat=True))


def _validate_single_select(cf, value):
    if not isinstance(value, str):
        raise ValidationError(_("Select one value."), code="INVALID_TYPE")
    if value not in _active_choice_keys(cf):
        raise ValidationError(_("Select a valid choice."), code="INVALID_CHOICE")
    return value


def _validate_multi_select(cf, value):
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValidationError(_("Select a list of values."), code="INVALID_TYPE")
    valid_keys = _active_choice_keys(cf)
    if len(value) != len(set(value)) or any(item not in valid_keys for item in value):
        raise ValidationError(_("Select unique valid choices."), code="INVALID_CHOICE")
    if cf.max_values is not None and len(value) > cf.max_values:
        raise ValidationError(_("Too many values were selected."), code="INVALID_RANGE")
    return sorted(value)


VALUE_VALIDATORS = {
    CustomField.FIELD_TYPE_TEXT: _validate_text,
    CustomField.FIELD_TYPE_INTEGER: _validate_integer,
    CustomField.FIELD_TYPE_DECIMAL: _canonical_decimal,
    CustomField.FIELD_TYPE_DATE: _validate_date,
    CustomField.FIELD_TYPE_BOOLEAN: _validate_boolean,
    CustomField.FIELD_TYPE_SINGLE_SELECT: _validate_single_select,
    CustomField.FIELD_TYPE_MULTI_SELECT: _validate_multi_select,
}


def validate_custom_field_value(cf, value, merged_values=None):
    """Validate and canonicalize one typed custom-field value."""
    cf = _definition(cf)
    if value is None:
        if cf.nullable:
            return None
        raise ValidationError(_("Null is not allowed for this field."), code="INVALID_VALUE")
    validator = VALUE_VALIDATORS.get(cf.field_type)
    if validator is None:
        raise ValidationError(_("The field type is unsupported."), code="INVALID_TYPE")
    return validator(cf, value)


def apply_custom_field_patch(existing, definitions, submitted, clear_keys=()):
    """Apply an explicit merge patch while preserving every unmentioned key."""
    definitions_by_key = {_definition(item).name: item for item in definitions}
    submitted_keys = set(submitted)
    clear_keys = set(clear_keys)
    if submitted_keys & clear_keys:
        raise ValidationError(_("A field cannot be set and cleared together."), code="CONFLICT_CLEAR_OVERLAP")
    if (submitted_keys | clear_keys) - definitions_by_key.keys():
        raise ValidationError(_("Unknown custom field key."), code="UNKNOWN_FIELD_KEY")

    for key in submitted_keys | clear_keys:
        if getattr(definitions_by_key[key], "read_only", False):
            raise ValidationError(_("Deprecated custom fields are read-only."), code="READ_ONLY_FIELD")

    merged = dict(existing or {})
    for key in clear_keys:
        merged.pop(key, None)
    for key, value in submitted.items():
        merged[key] = validate_custom_field_value(definitions_by_key[key], value, merged)

    maximum = definitions_by_key.get("operating_temperature_max")
    if maximum and maximum.validation_rule == "temperature_max_gte_min":
        minimum_value = merged.get("operating_temperature_min")
        maximum_value = merged.get("operating_temperature_max")
        if minimum_value is not None and maximum_value is not None:
            if Decimal(maximum_value) < Decimal(minimum_value):
                raise ValidationError(_("Maximum temperature must not be below minimum."), code="INVALID_RANGE")
    return merged


def build_custom_field_form_field(cf, initial_value=None, read_only=False):
    """Build one Django field from the relational definition."""
    cf = _definition(cf)
    common = {
        "label": cf.label,
        "help_text": cf.help_text,
        "required": cf.required,
        "initial": initial_value,
        "disabled": read_only,
    }
    if cf.field_type == CustomField.FIELD_TYPE_TEXT:
        return forms.CharField(
            max_length=cf.text_max_length, widget=forms.TextInput(attrs={"class": "form-control"}), **common
        )
    if cf.field_type == CustomField.FIELD_TYPE_INTEGER:
        return forms.IntegerField(widget=forms.NumberInput(attrs={"class": "form-control"}), **common)
    if cf.field_type == CustomField.FIELD_TYPE_DECIMAL:
        return forms.DecimalField(
            max_digits=24,
            decimal_places=cf.decimal_scale,
            widget=forms.NumberInput(attrs={"class": "form-control"}),
            **common,
        )
    if cf.field_type == CustomField.FIELD_TYPE_DATE:
        return forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}), **common)
    if cf.field_type == CustomField.FIELD_TYPE_BOOLEAN:
        common["initial"] = initial_value if initial_value is not None else False
        return forms.BooleanField(widget=forms.CheckboxInput(attrs={"class": "form-check-input"}), **common)
    if cf.field_type == CustomField.FIELD_TYPE_SINGLE_SELECT:
        choices = list(cf.choice_set.choices.filter(lifecycle=CustomField.LIFECYCLE_ACTIVE).values_list("key", "label"))
        return forms.ChoiceField(
            choices=[("", "---------"), *choices],
            widget=forms.Select(attrs={"class": "form-select"}),
            **common,
        )
    if cf.field_type == CustomField.FIELD_TYPE_MULTI_SELECT:
        choices = list(cf.choice_set.choices.filter(lifecycle=CustomField.LIFECYCLE_ACTIVE).values_list("key", "label"))
        return forms.MultipleChoiceField(
            choices=choices,
            widget=forms.SelectMultiple(attrs={"class": "form-select"}),
            **common,
        )
    return None


def serialize_custom_field_value(value, definition=None):
    """Normalize a cleaned form value for JSON storage."""
    if definition is not None:
        return validate_custom_field_value(definition, value)
    if value is None:
        return None
    if isinstance(value, (int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def custom_fields_for_model(model, include_inactive=False):
    """CustomFields whose object_types include the given model."""
    ct = ContentType.objects.get_for_model(model)
    queryset = CustomField.objects.filter(object_types=ct)
    if not include_inactive:
        queryset = queryset.filter(lifecycle=CustomField.LIFECYCLE_ACTIVE)
    return queryset


def _custom_field_filter_lookup(definition, name, value):
    if definition.field_type == CustomField.FIELD_TYPE_BOOLEAN and isinstance(value, str):
        if value.casefold() not in ("true", "false"):
            return None
        value = value.casefold() == "true"
    elif definition.field_type == CustomField.FIELD_TYPE_INTEGER and isinstance(value, str):
        try:
            value = int(value)
        except ValueError:
            return None
    try:
        if definition.field_type == CustomField.FIELD_TYPE_MULTI_SELECT:
            value = validate_custom_field_value(definition, [value])[0]
            return f"custom_field_data__{name}__contains", [value]
        return f"custom_field_data__{name}", validate_custom_field_value(definition, value)
    except ValidationError:
        return None


def apply_custom_field_filters(queryset, model, params):
    """Filter a queryset by ``cf_<name>=<value>`` request parameters."""
    definitions = {field.name: field for field in custom_fields_for_model(model)}
    for param, value in params.items():
        if not param.startswith("cf_") or value in (None, ""):
            continue
        name = param[3:]
        definition = definitions.get(name)
        if definition is None:
            continue
        lookup = _custom_field_filter_lookup(definition, name, value)
        if lookup is None:
            return queryset.none()
        lookup_name, lookup_value = lookup
        queryset = queryset.filter(**{lookup_name: lookup_value})
    return queryset


class CustomFieldModelFormMixin:
    """ModelForm mixin that renders and merge-persists generic custom fields."""

    custom_fields_fieldset_label = _("Custom Fields")

    def get_custom_field_definitions(self):
        return custom_fields_for_model(self._meta.model, include_inactive=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.custom_field_keys = getattr(self, "custom_field_keys", [])
        self.custom_field_definitions = getattr(self, "custom_field_definitions", {})
        self.custom_field_clear_keys = getattr(self, "custom_field_clear_keys", {})
        stored = {}
        if self.instance is not None and self.instance.pk:
            stored = dict(getattr(self.instance, "custom_field_data", None) or {})

        for item in self.get_custom_field_definitions():
            cf = _definition(item)
            if cf.deleted_at is not None:
                continue
            if cf.lifecycle == CustomField.LIFECYCLE_DEPRECATED and cf.name not in stored:
                continue
            key = f"cf_{cf.name}"
            if key in self.fields:
                continue
            form_field = build_custom_field_form_field(
                cf,
                stored.get(cf.name),
                read_only=getattr(item, "read_only", False) or cf.lifecycle == CustomField.LIFECYCLE_DEPRECATED,
            )
            if form_field is not None:
                self.fields[key] = form_field
                self.custom_field_keys.append(key)
                self.custom_field_definitions[key] = cf
                if not form_field.disabled:
                    clear_key = custom_field_clear_key(cf.name)
                    self.fields[clear_key] = build_custom_field_clear_form_field()
                    self.custom_field_clear_keys[key] = clear_key

    def append_custom_fields_to_layout(self):
        """Append injected cf_ fields to an existing crispy helper layout."""
        if not self.custom_field_keys:
            return
        helper = getattr(self, "helper", None)
        if helper is None or helper.layout is None:
            return
        rows = []
        for index in range(0, len(self.custom_field_keys), 2):
            chunk = self.custom_field_keys[index : index + 2]
            cells = []
            for key in chunk:
                clear_key = self.custom_field_clear_keys.get(key)
                fields = [key]
                if clear_key:
                    fields.append(clear_key)
                cells.append(Div(*fields, css_class="col-md-6"))
            rows.append(Div(*cells, css_class="row"))
        helper.layout.append(Fieldset(self.custom_fields_fieldset_label, *rows, css_class="mb-4 border p-3 rounded"))

    def clean(self):
        cleaned_data = super().clean()
        return clean_custom_field_form_values(
            self,
            cleaned_data,
            self.custom_field_definitions,
            self.custom_field_clear_keys,
        )

    def save(self, commit=True):
        instance = super().save(commit=False)
        submitted = {}
        clear_keys = set()
        for key, definition in self.custom_field_definitions.items():
            if key not in self.cleaned_data or self.fields[key].disabled:
                continue
            value = self.cleaned_data[key]
            clear_key = self.custom_field_clear_keys.get(key)
            if clear_key and self.cleaned_data.get(clear_key):
                clear_keys.add(definition.name)
                continue
            if is_omitted_optional_single_select(definition, value):
                continue
            if value is None and not definition.nullable:
                continue
            submitted[definition.name] = value
        if submitted or clear_keys:
            instance.custom_field_data = apply_custom_field_patch(
                getattr(instance, "custom_field_data", None) or {},
                self.custom_field_definitions.values(),
                submitted,
                clear_keys=clear_keys,
            )
        if commit:
            instance.save()
            self.save_m2m()
        return instance
