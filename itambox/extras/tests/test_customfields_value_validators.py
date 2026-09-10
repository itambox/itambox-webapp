"""Branch coverage for the extras custom-field value validators."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django import forms
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from extras.customfields import (
    JCS_INTEGER_MAX,
    CustomFieldModelFormMixin,
    _canonical_decimal,
    _custom_field_filter_lookup,
    _parse_decimal,
    _validate_boolean,
    _validate_date,
    _validate_hostname,
    _validate_integer,
    _validate_text,
    apply_custom_field_patch,
    build_custom_field_clear_form_field,
    build_custom_field_form_field,
    clean_custom_field_form_values,
    custom_field_clear_key,
    serialize_custom_field_value,
    validate_custom_field_data_values,
    validate_custom_field_regex,
    validate_custom_field_value,
    validate_required_custom_field_values,
)


class _CF(SimpleNamespace):
    def __init__(self, **kwargs):
        defaults = dict(
            decimal_scale=2,
            minimum_value=None,
            maximum_value=None,
            text_max_length=None,
            regex=None,
            validation_rule=None,
            required=False,
        )
        defaults.update(kwargs)
        super().__init__(**defaults)


class DecimalValidationTests(SimpleTestCase):
    def test_parse_rejects_bools_and_none(self):
        for bad in (True, False, None):
            with self.assertRaises(ValidationError) as ctx:
                _parse_decimal(_CF(), bad)
            self.assertEqual(ctx.exception.code, "INVALID_TYPE")

    def test_parse_rejects_invalid_scale(self):
        for scale in (None, -1, 7):
            with self.assertRaises(ValidationError) as ctx:
                _parse_decimal(_CF(decimal_scale=scale), "1.5")
            self.assertEqual(ctx.exception.code, "INVALID_RANGE")

    def test_parse_rejects_exponents_and_plus_signs(self):
        for bad in ("1e3", "1E3", "+5"):
            with self.assertRaises(ValidationError) as ctx:
                _parse_decimal(_CF(), bad)
            self.assertEqual(ctx.exception.code, "INVALID_VALUE")

    def test_parse_rejects_non_canonical_grammar(self):
        for bad in ("1.234", "00.5", "01", "1,5", "-", "abc", "1."):
            with self.assertRaises(ValidationError) as ctx:
                _parse_decimal(_CF(), bad)
            self.assertEqual(ctx.exception.code, "INVALID_VALUE")

    def test_parse_rejects_too_many_integer_digits(self):
        with self.assertRaises(ValidationError) as ctx:
            _parse_decimal(_CF(), "1234567890123456789")
        self.assertEqual(ctx.exception.code, "INVALID_RANGE")

    def test_parse_rejects_negative_zero(self):
        with self.assertRaises(ValidationError) as ctx:
            _parse_decimal(_CF(), "-0")
        self.assertEqual(ctx.exception.code, "INVALID_VALUE")

    def test_parse_accepts_canonical_values(self):
        self.assertEqual(str(_parse_decimal(_CF(), "12.5")), "12.5")
        self.assertEqual(str(_parse_decimal(_CF(), "-3")), "-3")
        self.assertEqual(str(_parse_decimal(_CF(decimal_scale=0), "7")), "7")
        self.assertEqual(str(_parse_decimal(_CF(), "0")), "0")

    def test_canonical_enforces_minimum_and_maximum(self):
        with self.assertRaises(ValidationError) as ctx:
            _canonical_decimal(_CF(minimum_value=Decimal("5.00")), "4")
        self.assertEqual(ctx.exception.code, "INVALID_RANGE")
        with self.assertRaises(ValidationError) as ctx:
            _canonical_decimal(_CF(maximum_value=Decimal("5.00")), "6")
        self.assertEqual(ctx.exception.code, "INVALID_RANGE")
        self.assertEqual(
            _canonical_decimal(_CF(minimum_value=Decimal("5.00"), maximum_value=Decimal("6.00")), "5.5"), "5.50"
        )


class HostnameValidationTests(SimpleTestCase):
    def test_rejects_invalid_hostnames(self):
        for bad in (5, "", "a" * 254, "host.", "ho st", "-host.example", "host.-example"):
            self.assertFalse(_validate_hostname(bad))

    def test_accepts_valid_hostnames(self):
        for good in ("example.com", "h", "a-b.example.com", "host.example.com"):
            self.assertTrue(_validate_hostname(good))


class TextValidationTests(SimpleTestCase):
    def test_rejects_non_strings(self):
        with self.assertRaises(ValidationError) as ctx:
            _validate_text(_CF(), 5)
        self.assertEqual(ctx.exception.code, "INVALID_TYPE")

    def test_optional_empty_text_passes_through(self):
        self.assertEqual(_validate_text(_CF(), ""), "")

    def test_enforces_max_length(self):
        with self.assertRaises(ValidationError) as ctx:
            _validate_text(_CF(text_max_length=2), "abc")
        self.assertEqual(ctx.exception.code, "INVALID_RANGE")

    def test_enforces_regex(self):
        validate_custom_field_regex(r"^[a-z]+$")
        with self.assertRaises(ValidationError) as ctx:
            _validate_text(_CF(regex=r"^[a-z]+$"), "ABC")
        self.assertEqual(ctx.exception.code, "INVALID_VALUE")
        self.assertEqual(_validate_text(_CF(regex=r"^[a-z]+$"), "abc"), "abc")

    def test_enforces_hostname_rule(self):
        with self.assertRaises(ValidationError) as ctx:
            _validate_text(_CF(validation_rule="rfc1123_hostname"), "not a host")
        self.assertEqual(ctx.exception.code, "INVALID_VALUE")
        self.assertEqual(_validate_text(_CF(validation_rule="rfc1123_hostname"), "example.com"), "example.com")


class IntegerValidationTests(SimpleTestCase):
    def test_rejects_bools_and_non_integers(self):
        for bad in (True, 1.5, "3"):
            with self.assertRaises(ValidationError) as ctx:
                _validate_integer(_CF(), bad)
            self.assertEqual(ctx.exception.code, "INVALID_TYPE")

    def test_enforces_minimum_and_maximum(self):
        with self.assertRaises(ValidationError) as ctx:
            _validate_integer(_CF(minimum_value=Decimal("3")), 2)
        self.assertEqual(ctx.exception.code, "INVALID_RANGE")
        with self.assertRaises(ValidationError) as ctx:
            _validate_integer(_CF(maximum_value=Decimal("3")), 4)
        self.assertEqual(ctx.exception.code, "INVALID_RANGE")
        self.assertEqual(_validate_integer(_CF(minimum_value=Decimal("3"), maximum_value=Decimal("5")), 4), 4)


class DateValidationTests(SimpleTestCase):
    def test_accepts_date_objects_and_canonical_strings(self):
        self.assertEqual(_validate_date(_CF(), date(2026, 1, 2)), "2026-01-02")
        self.assertEqual(_validate_date(_CF(), "2026-01-02"), "2026-01-02")

    def test_rejects_non_strings_and_bad_dates(self):
        with self.assertRaises(ValidationError) as ctx:
            _validate_date(_CF(), 5)
        self.assertEqual(ctx.exception.code, "INVALID_TYPE")
        for bad in ("2026-13-01", "not-a-date", "2026-1-2", "2026-01-02T00:00:00"):
            with self.assertRaises(ValidationError) as ctx:
                _validate_date(_CF(), bad)
            self.assertEqual(ctx.exception.code, "INVALID_VALUE")


class BooleanValidationTests(SimpleTestCase):
    def test_rejects_non_booleans(self):
        for bad in (1, "true", None):
            with self.assertRaises(ValidationError) as ctx:
                _validate_boolean(_CF(), bad)
            self.assertEqual(ctx.exception.code, "INVALID_TYPE")

    def test_accepts_booleans(self):
        self.assertIs(_validate_boolean(_CF(), True), True)
        self.assertIs(_validate_boolean(_CF(), False), False)


# ---------------------------------------------------------------------------
# Batch E: required-value presence, definition-aware validation, merge patch,
# filter lookups, form-field construction, form cleaning and layout injection.
# ---------------------------------------------------------------------------


def _definition_obj(name, field_type, **overrides):
    values = dict(
        name=name,
        field_type=field_type,
        required=False,
        nullable=True,
        lifecycle="active",
        label=name,
        help_text="",
        decimal_scale=2,
        minimum_value=None,
        maximum_value=None,
        text_max_length=None,
        max_values=None,
        regex=None,
        validation_rule=None,
        choice_set=None,
        choice_set_id=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _choice(key, label, position, lifecycle="active"):
    return SimpleNamespace(key=key, label=label, position=position, lifecycle=lifecycle)


class _Choices(list):
    def filter(self, **kwargs):
        return _Choices(
            [item for item in self if all(getattr(item, key, None) == value for key, value in kwargs.items())]
        )


def _choice_set(*choices, lifecycle="active"):
    return SimpleNamespace(lifecycle=lifecycle, choices=_Choices(choices))


class _FakeFormField:
    __slots__ = ("disabled",)

    def __init__(self, disabled=False):
        self.disabled = disabled


class _FakeForm:
    __slots__ = ("errors", "fields")

    def __init__(self, keys=(), disabled_keys=()):
        self.fields = {key: _FakeFormField(disabled=key in disabled_keys) for key in keys}
        self.errors = {}

    def add_error(self, key, error):
        self.errors.setdefault(key, []).append(error)


class RequiredValuePresenceTests(SimpleTestCase):
    def test_required_presence_matrix_per_field_type(self):
        present = [
            ("text", "value"),
            ("date", "2024-01-02"),
            ("single-select", "usb_c"),
            ("multi-select", ["usb_c"]),
            ("integer", 5),
            ("decimal", "1.50"),
            ("boolean", False),
            ("unsupported-type", "anything"),
        ]
        absent = [
            ("text", ""),
            ("date", ""),
            ("single-select", ""),
            ("multi-select", []),
            ("multi-select", "usb_c"),
            ("multi-select", [1]),
            ("integer", True),
            ("decimal", ""),
            ("boolean", "true"),
        ]

        for field_type, value in present:
            with self.subTest(field_type=field_type, value=value, expectation="present"):
                validate_required_custom_field_values(
                    [_definition_obj("field", field_type, required=True)], {"field": value}
                )

        for field_type, value in absent:
            with self.subTest(field_type=field_type, value=value, expectation="absent"):
                with self.assertRaises(ValidationError) as ctx:
                    validate_required_custom_field_values(
                        [_definition_obj("field", field_type, required=True)], {"field": value}
                    )
                self.assertEqual(
                    {key: [str(item) for item in value] for key, value in ctx.exception.message_dict.items()},
                    {"field": ["This field is required."]},
                )

    def test_none_is_never_present_even_for_nullable_definitions(self):
        definition = _definition_obj("field", "boolean", required=True, nullable=True)

        with self.assertRaises(ValidationError) as ctx:
            validate_required_custom_field_values([definition], {"field": None})

        self.assertEqual(
            {key: [str(item) for item in value] for key, value in ctx.exception.message_dict.items()},
            {"field": ["This field is required."]},
        )

    def test_deprecated_required_definition_only_applies_to_stored_values(self):
        definition = _definition_obj("legacy", "text", required=True, lifecycle="deprecated")

        validate_required_custom_field_values([definition], {})

        with self.assertRaises(ValidationError):
            validate_required_custom_field_values([definition], {"legacy": ""})


class CustomFieldValueContractTests(SimpleTestCase):
    def test_null_is_allowed_only_for_nullable_definitions(self):
        self.assertIsNone(validate_custom_field_value(_definition_obj("note", "text", nullable=True), None))

        with self.assertRaises(ValidationError) as ctx:
            validate_custom_field_value(_definition_obj("note", "text", nullable=False), None)

        self.assertEqual(ctx.exception.code, "INVALID_VALUE")

    def test_unsupported_field_types_are_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_custom_field_value(_definition_obj("odd", "unsupported-type"), "value")

        self.assertEqual(ctx.exception.code, "INVALID_TYPE")

    def test_read_only_and_deprecated_values_skip_typed_validation(self):
        read_only = _definition_obj("legacy", "integer", read_only=True)
        deprecated = _definition_obj("retired", "integer", lifecycle="deprecated")

        validate_custom_field_data_values([read_only], {"legacy": "not-an-integer"})
        validate_custom_field_data_values([deprecated], {"retired": "not-an-integer"})

    def test_cross_field_temperature_rule_compares_both_stored_values(self):
        definitions = [
            _definition_obj("operating_temperature_min", "decimal"),
            _definition_obj("operating_temperature_max", "decimal", validation_rule="temperature_max_gte_min"),
        ]

        validate_custom_field_data_values(
            definitions, {"operating_temperature_min": "10.00", "operating_temperature_max": "20.00"}
        )

        with self.assertRaises(ValidationError) as ctx:
            validate_custom_field_data_values(
                definitions, {"operating_temperature_min": "20.00", "operating_temperature_max": "10.00"}
            )

        self.assertEqual(ctx.exception.code, "INVALID_RANGE")

    def test_cross_field_rule_is_skipped_while_only_one_side_is_stored(self):
        definitions = [
            _definition_obj("operating_temperature_min", "decimal"),
            _definition_obj("operating_temperature_max", "decimal", validation_rule="temperature_max_gte_min"),
        ]

        validate_custom_field_data_values(definitions, {"operating_temperature_max": "5.00"})


class CustomFieldPatchTests(SimpleTestCase):
    def test_set_and_clear_overlap_is_rejected(self):
        definition = _definition_obj("memory_capacity", "integer")

        with self.assertRaises(ValidationError) as ctx:
            apply_custom_field_patch({}, [definition], {"memory_capacity": 8}, ["memory_capacity"])

        self.assertEqual(ctx.exception.code, "CONFLICT_CLEAR_OVERLAP")

    def test_unknown_keys_are_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            apply_custom_field_patch({}, [_definition_obj("memory_capacity", "integer")], {"unknown_key": 1})

        self.assertEqual(ctx.exception.code, "UNKNOWN_FIELD_KEY")

    def test_read_only_definitions_reject_set_and_clear_patches(self):
        definition = _definition_obj("legacy", "integer", read_only=True)

        for submitted, clear in (({"legacy": 5}, []), ({}, ["legacy"])):
            with self.subTest(submitted=submitted, clear=clear):
                with self.assertRaises(ValidationError) as ctx:
                    apply_custom_field_patch({}, [definition], submitted, clear)
                self.assertEqual(ctx.exception.code, "READ_ONLY_FIELD")

    def test_clear_removes_only_the_requested_key(self):
        definitions = [_definition_obj("memory_capacity", "integer"), _definition_obj("label", "text")]

        merged = apply_custom_field_patch({"memory_capacity": 8, "label": "A"}, definitions, {}, ["memory_capacity"])

        self.assertEqual(merged, {"label": "A"})

    def test_submitted_values_are_canonicalised_without_touching_other_keys(self):
        definitions = [_definition_obj("memory_capacity", "decimal"), _definition_obj("label", "text")]

        merged = apply_custom_field_patch({"label": "A"}, definitions, {"memory_capacity": "8.5"})

        self.assertEqual(merged, {"label": "A", "memory_capacity": "8.50"})


class CustomFieldFilterLookupTests(SimpleTestCase):
    def test_boolean_and_integer_string_values_are_coerced(self):
        boolean = _definition_obj("enabled", "boolean")

        self.assertEqual(_custom_field_filter_lookup(boolean, "enabled", "true"), ("custom_field_data__enabled", True))
        self.assertEqual(
            _custom_field_filter_lookup(boolean, "enabled", "FALSE"), ("custom_field_data__enabled", False)
        )
        self.assertIsNone(_custom_field_filter_lookup(boolean, "enabled", "maybe"))

        integer = _definition_obj("count", "integer")

        self.assertEqual(_custom_field_filter_lookup(integer, "count", "3"), ("custom_field_data__count", 3))
        self.assertIsNone(_custom_field_filter_lookup(integer, "count", "three"))

    def test_multi_select_values_compile_into_a_containment_lookup(self):
        definition = _definition_obj(
            "ports", "multi-select", choice_set=_choice_set(_choice("usb_c", "USB-C", 1)), choice_set_id=1
        )

        self.assertEqual(
            _custom_field_filter_lookup(definition, "ports", "usb_c"),
            ("custom_field_data__ports__contains", ["usb_c"]),
        )
        self.assertIsNone(_custom_field_filter_lookup(definition, "ports", "unknown_choice"))

    def test_typed_values_use_the_plain_key_lookup(self):
        definition = _definition_obj("label", "text")

        self.assertEqual(_custom_field_filter_lookup(definition, "label", "Rack"), ("custom_field_data__label", "Rack"))


class SerializeCustomFieldValueTests(SimpleTestCase):
    def test_definition_aware_serialization_validates_the_value(self):
        self.assertEqual(serialize_custom_field_value("2024-01-02", _definition_obj("seen_on", "date")), "2024-01-02")

        with self.assertRaises(ValidationError):
            serialize_custom_field_value("not-a-date", _definition_obj("seen_on", "date"))

    def test_definition_free_serialization_preserves_json_shapes(self):
        self.assertIsNone(serialize_custom_field_value(None))
        self.assertIs(serialize_custom_field_value(True), True)
        self.assertEqual(serialize_custom_field_value(date(2024, 1, 2)), "2024-01-02")
        self.assertEqual(serialize_custom_field_value(Decimal("1.50")), "1.50")


class CustomFieldFormFieldTests(SimpleTestCase):
    def test_multi_select_field_lists_active_choice_rows_in_order(self):
        choice_set = _choice_set(
            _choice("usb_c", "USB-C", 1),
            _choice("hdmi", "HDMI", 2),
            _choice("retired", "Retired", 3, lifecycle="deprecated"),
        )
        definition = _definition_obj("ports", "multi-select", choice_set=choice_set, choice_set_id=1, max_values=4)

        field = build_custom_field_form_field(definition)

        self.assertIsInstance(field, forms.MultipleChoiceField)
        self.assertEqual(field.choices, [("usb_c", "USB-C"), ("hdmi", "HDMI")])

    def test_choice_rows_can_come_from_the_prefetched_cache(self):
        cached = _Choices([_choice("hdd", "HDD", 2), _choice("ssd", "SSD", 1)])
        choice_set = _choice_set(_choice("usb_c", "USB-C", 9))
        choice_set._prefetched_objects_cache = {"choices": cached}
        definition = _definition_obj("storage", "multi-select", choice_set=choice_set, choice_set_id=1, max_values=2)

        field = build_custom_field_form_field(definition)

        self.assertEqual(field.choices, [("hdd", "HDD"), ("ssd", "SSD")])


class CustomFieldFormCleanTests(SimpleTestCase):
    def test_disabled_fields_and_clear_flags_are_skipped(self):
        form = _FakeForm(keys=("cf_locked", "cf_label", "cf_label__clear"), disabled_keys=("cf_locked",))
        definitions = {"cf_locked": _definition_obj("locked", "integer"), "cf_label": _definition_obj("label", "text")}
        cleaned = {"cf_locked": "not-an-integer", "cf_label": "value", "cf_label__clear": True}

        result = clean_custom_field_form_values(form, cleaned, definitions, {"cf_label": "cf_label__clear"})

        self.assertEqual(result["cf_label"], "value")
        self.assertEqual(form.errors, {})

    def test_null_values_for_non_nullable_definitions_are_left_untouched(self):
        form = _FakeForm(keys=("cf_note",))
        definitions = {"cf_note": _definition_obj("note", "text", nullable=False)}
        cleaned = {"cf_note": None}

        clean_custom_field_form_values(form, cleaned, definitions)

        self.assertIsNone(cleaned["cf_note"])
        self.assertEqual(form.errors, {})

    def test_invalid_values_are_attached_to_the_form(self):
        form = _FakeForm(keys=("cf_count",))
        definitions = {"cf_count": _definition_obj("count", "integer")}
        cleaned = {"cf_count": "not-an-integer"}

        clean_custom_field_form_values(form, cleaned, definitions)

        self.assertEqual(set(form.errors), {"cf_count"})

    def test_valid_values_are_canonicalised_in_place(self):
        form = _FakeForm(keys=("cf_count", "cf_seen"))
        definitions = {"cf_count": _definition_obj("count", "integer"), "cf_seen": _definition_obj("seen", "date")}
        cleaned = {"cf_count": 4, "cf_seen": date(2024, 1, 2)}

        clean_custom_field_form_values(form, cleaned, definitions)

        self.assertEqual(cleaned, {"cf_count": 4, "cf_seen": "2024-01-02"})
        self.assertEqual(form.errors, {})


class _LayoutForm(CustomFieldModelFormMixin):
    custom_fields_fieldset_label = "Custom Fields"

    def __init__(self, keys, clear_keys):
        self.custom_field_keys = list(keys)
        self.custom_field_clear_keys = dict(clear_keys)
        self.helper = SimpleNamespace(layout=[])


class CustomFieldFormLayoutTests(SimpleTestCase):
    def test_layout_rows_pair_fields_with_their_clear_checkboxes(self):
        form = _LayoutForm(["cf_memory", "cf_ports", "cf_label"], {"cf_memory": "cf_memory__clear"})

        form.append_custom_fields_to_layout()

        fieldset = form.helper.layout[-1]
        self.assertEqual(len(fieldset.fields), 2)
        self.assertEqual(list(fieldset.fields[0].fields[0].fields), ["cf_memory", "cf_memory__clear"])
        self.assertEqual(list(fieldset.fields[0].fields[1].fields), ["cf_ports"])
        self.assertEqual(list(fieldset.fields[1].fields[0].fields), ["cf_label"])

    def test_layout_is_untouched_without_injected_fields(self):
        form = _LayoutForm([], {})

        form.append_custom_fields_to_layout()

        self.assertEqual(form.helper.layout, [])


class CustomFieldValueBoundaryTests(SimpleTestCase):
    def test_integer_bounds_follow_the_jcs_safe_integer_range(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_custom_field_value(_definition_obj("count", "integer"), JCS_INTEGER_MAX + 1)

        self.assertEqual(ctx.exception.code, "INVALID_RANGE")
        self.assertEqual(
            validate_custom_field_value(_definition_obj("count", "integer"), JCS_INTEGER_MAX), JCS_INTEGER_MAX
        )

    def test_non_canonical_iso_dates_are_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_custom_field_value(_definition_obj("seen_on", "date"), "20240102")

        self.assertEqual(ctx.exception.code, "INVALID_VALUE")

    def test_single_select_requires_an_active_choice_set(self):
        stored_only = _definition_obj("kind", "single-select")
        inactive = _definition_obj(
            "kind",
            "single-select",
            choice_set=_choice_set(_choice("a", "A", 1), lifecycle="deprecated"),
            choice_set_id=1,
        )
        active = _definition_obj("kind", "single-select", choice_set=_choice_set(_choice("a", "A", 1)), choice_set_id=1)

        with self.assertRaises(ValidationError) as ctx:
            validate_custom_field_value(stored_only, "a")
        self.assertEqual(ctx.exception.code, "INVALID_CHOICE")

        with self.assertRaises(ValidationError) as ctx:
            validate_custom_field_value(inactive, "a")
        self.assertEqual(ctx.exception.code, "INVALID_CHOICE")

        with self.assertRaises(ValidationError) as ctx:
            validate_custom_field_value(active, "unknown")
        self.assertEqual(ctx.exception.code, "INVALID_CHOICE")

        with self.assertRaises(ValidationError) as ctx:
            validate_custom_field_value(active, ["a"])
        self.assertEqual(ctx.exception.code, "INVALID_TYPE")

        self.assertEqual(validate_custom_field_value(active, "a"), "a")

    def test_multi_select_rejects_wrong_shapes_duplicates_and_overflow(self):
        definition = _definition_obj(
            "ports",
            "multi-select",
            choice_set=_choice_set(_choice("usb_c", "USB-C", 2), _choice("hdmi", "HDMI", 1)),
            choice_set_id=1,
            max_values=2,
        )

        with self.assertRaises(ValidationError) as ctx:
            validate_custom_field_value(definition, "usb_c")
        self.assertEqual(ctx.exception.code, "INVALID_TYPE")

        with self.assertRaises(ValidationError) as ctx:
            validate_custom_field_value(definition, ["usb_c", "usb_c"])
        self.assertEqual(ctx.exception.code, "INVALID_CHOICE")

        with self.assertRaises(ValidationError) as ctx:
            validate_custom_field_value(definition, ["usb_c", "nope"])
        self.assertEqual(ctx.exception.code, "INVALID_CHOICE")

        overflow = _definition_obj(
            "ports", "multi-select", choice_set=definition.choice_set, choice_set_id=1, max_values=1
        )
        with self.assertRaises(ValidationError) as ctx:
            validate_custom_field_value(overflow, ["usb_c", "hdmi"])
        self.assertEqual(ctx.exception.code, "INVALID_RANGE")

        self.assertEqual(validate_custom_field_value(definition, ["usb_c", "hdmi"]), ["hdmi", "usb_c"])

    def test_unknown_stored_keys_are_ignored_by_validation(self):
        validate_custom_field_data_values([_definition_obj("known", "text")], {"kind_of_unknown": "kept"})

    def test_clear_keys_follow_the_documented_naming_scheme(self):
        self.assertEqual(custom_field_clear_key("memory_capacity"), "cf_memory_capacity__clear")

        field = build_custom_field_clear_form_field()
        self.assertIsInstance(field, forms.BooleanField)
        self.assertFalse(field.required)
        self.assertEqual(str(field.label), "Remove value")


class CustomFieldFormFieldVariantTests(SimpleTestCase):
    def _definition(self, field_type, **overrides):
        return _definition_obj(
            "field",
            field_type,
            choice_set=_choice_set(_choice("a", "A", 1), _choice("b", "B", 2)),
            choice_set_id=1,
            max_values=2,
            decimal_scale=2,
            text_max_length=32,
            **overrides,
        )

    def test_text_field_carries_the_declared_maximum_length(self):
        field = build_custom_field_form_field(self._definition("text"))

        self.assertIsInstance(field, forms.CharField)
        self.assertEqual(field.max_length, 32)

    def test_integer_and_decimal_fields_use_their_number_metadata(self):
        integer = build_custom_field_form_field(self._definition("integer"))
        decimal = build_custom_field_form_field(self._definition("decimal"))

        self.assertIsInstance(integer, forms.IntegerField)
        self.assertIsInstance(decimal, forms.DecimalField)
        self.assertEqual(decimal.decimal_places, 2)

    def test_date_and_boolean_fields_use_their_widgets(self):
        date_field = build_custom_field_form_field(self._definition("date"))
        optional_boolean = build_custom_field_form_field(self._definition("boolean"))
        required_boolean = build_custom_field_form_field(self._definition("boolean", required=True))

        self.assertIsInstance(date_field, forms.DateField)
        self.assertIsInstance(optional_boolean, forms.BooleanField)
        self.assertFalse(optional_boolean.initial)
        self.assertIsInstance(required_boolean, forms.TypedChoiceField)
        self.assertIs(required_boolean.coerce("true"), True)
        self.assertIs(required_boolean.coerce("no"), False)

    def test_select_fields_list_their_active_choices(self):
        single = build_custom_field_form_field(self._definition("single-select"))
        multiple = build_custom_field_form_field(self._definition("multi-select"))

        self.assertIsInstance(single, forms.ChoiceField)
        self.assertEqual(single.choices, [("", "---------"), ("a", "A"), ("b", "B")])
        self.assertIsInstance(multiple, forms.MultipleChoiceField)
        self.assertEqual(multiple.choices, [("a", "A"), ("b", "B")])

    def test_unsupported_field_types_build_no_form_field(self):
        self.assertIsNone(build_custom_field_form_field(self._definition("unsupported-type")))

    def test_read_only_definitions_disable_the_form_field(self):
        field = build_custom_field_form_field(self._definition("text"), read_only=True)

        self.assertTrue(field.disabled)

    def test_optional_single_select_blank_is_left_untouched_when_cleaning(self):
        form = _FakeForm(keys=("cf_kind",))
        definitions = {
            "cf_kind": _definition_obj(
                "kind", "single-select", choice_set=_choice_set(_choice("a", "A", 1)), choice_set_id=1
            )
        }
        cleaned = {"cf_kind": ""}

        clean_custom_field_form_values(form, cleaned, definitions)

        self.assertEqual(cleaned["cf_kind"], "")
        self.assertEqual(form.errors, {})
