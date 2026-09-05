"""Pure specification codec tests, including the T02 scalar oracle consumer."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from dataclasses import FrozenInstanceError, is_dataclass
from pathlib import Path
from types import MappingProxyType

from extras.services.specifications.codecs import (
    SpecificationCodecError,
    normalize_specification_patch,
    normalize_specification_value,
    order_multiselect_for_display,
)
from extras.services.specifications.contracts import (
    ChoiceDTO,
    ChoiceSetDTO,
    FieldDefinitionDTO,
    FieldKey,
    QualifiedIdentity,
    SpecificationValidationDTO,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "assets" / "tests" / "specification_contract_fixtures" / "scalars.json"
)
HISTORY_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "assets" / "tests" / "specification_contract_fixtures" / "history.json"
)


def validation(
    *,
    minimum: str | None = None,
    maximum: str | None = None,
    scale: int | None = None,
    max_length: int | None = None,
    max_values: int | None = None,
    regex: str | None = None,
    rule: str | None = None,
) -> SpecificationValidationDTO:
    return SpecificationValidationDTO(
        minimum=minimum,
        maximum=maximum,
        scale=scale,
        max_length=max_length,
        max_values=max_values,
        regex=regex,
        rule=rule,
    )


def choice_set(*, active: tuple[str, ...], deprecated: tuple[str, ...] = ()) -> ChoiceSetDTO:
    rows = tuple(
        ChoiceDTO(key=key, label=key, lifecycle="active", position=position) for position, key in enumerate(active)
    ) + tuple(
        ChoiceDTO(key=key, label=key, lifecycle="deprecated", position=position + len(active))
        for position, key in enumerate(deprecated)
    )
    return ChoiceSetDTO(
        identity=QualifiedIdentity("test/status"),
        label="Test choices",
        resource_revision="resource-1",
        lifecycle="active",
        choices=rows,
    )


def field(
    key: str,
    field_type: str,
    *,
    required: bool = False,
    nullable: bool = False,
    choice_set: ChoiceSetDTO | None = None,
    validation_data: SpecificationValidationDTO | None = None,
    lifecycle: str = "active",
) -> FieldDefinitionDTO:
    return FieldDefinitionDTO(
        resource_revision="resource-1",
        key=FieldKey(key),
        identity=QualifiedIdentity(f"test/{key}"),
        label=key,
        help_text="",
        targets=frozenset({"asset"}),
        activation="composed",
        field_type=field_type,
        quantity_kind=None,
        canonical_unit=None,
        validation=validation_data or validation(),
        required=required,
        nullable=nullable,
        lifecycle=lifecycle,
        choice_set=choice_set,
    )


def issue(error: SpecificationCodecError, index: int = 0):
    return error.issues[index]


def json_value(value):
    """Convert immutable multi-select output into JSON-shaped values for oracle comparison."""
    if isinstance(value, tuple):
        return [json_value(item) for item in value]
    if isinstance(value, MappingProxyType):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    return value


class SpecificationContractDTOTests(unittest.TestCase):
    def test_contract_dtos_are_frozen_and_import_without_django(self):
        dto = field("serial", "text")
        self.assertTrue(is_dataclass(dto))
        with self.assertRaises(FrozenInstanceError):
            dto.label = "changed"
        probe = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-c",
                "import importlib, sys; sys.path.insert(0, sys.argv[1]); "
                "importlib.import_module('extras.services.specifications.contracts'); "
                "importlib.import_module('extras.services.specifications.codecs'); "
                "assert not any(n == 'django' or n.startswith('django.') for n in sys.modules)",
                str(Path(__file__).resolve().parents[2]),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr)


class SpecificationValueCodecTests(unittest.TestCase):
    def test_text_preserves_whitespace_and_unicode(self):
        value = "  Café\t東京  "
        self.assertEqual(normalize_specification_value(field("note", "text"), value), value)

    def test_required_text_rejects_empty_and_whitespace_only(self):
        required = field("note", "text", required=True)
        for value in ("", "   ", "\t\n"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(SpecificationCodecError) as raised:
                    normalize_specification_value(required, value, path=("set", "note"))
                self.assertEqual(issue(raised.exception).code, "REQUIRED_FIELD")
                self.assertEqual(issue(raised.exception).path, ("set", "note"))

    def test_text_rejects_nul_unpaired_surrogates_and_tighter_length(self):
        bounded = field(
            "code",
            "text",
            validation_data=validation(max_length=3),
        )
        for value, expected_code in (("a\x00b", "INVALID_TYPE"), ("abcd", "INVALID_RANGE"), ("\ud800", "INVALID_TYPE")):
            with self.subTest(value=repr(value)):
                with self.assertRaises(SpecificationCodecError) as raised:
                    normalize_specification_value(bounded, value)
                self.assertEqual(issue(raised.exception).code, expected_code)

    def test_integer_accepts_zero_and_rejects_bool_strings_fraction_and_unsafe_values(self):
        bounded = field(
            "quantity",
            "integer",
            validation_data=validation(minimum="-2", maximum="2"),
        )
        self.assertEqual(normalize_specification_value(bounded, 0), 0)
        for value, expected_code in (
            (True, "INVALID_TYPE"),
            ("1", "INVALID_TYPE"),
            (1.5, "INVALID_TYPE"),
            (9007199254740992, "INVALID_RANGE"),
            (-3, "INVALID_RANGE"),
        ):
            with self.subTest(value=repr(value)):
                with self.assertRaises(SpecificationCodecError) as raised:
                    normalize_specification_value(bounded, value)
                self.assertEqual(issue(raised.exception).code, expected_code)

    def test_decimal_is_fixed_scale_without_float_conversion_or_rounding(self):
        money = field(
            "cost",
            "decimal",
            validation_data=validation(scale=2, minimum="-10.00", maximum="1000.00"),
        )
        self.assertEqual(normalize_specification_value(money, "12.3"), "12.30")
        self.assertEqual(normalize_specification_value(money, "0.00"), "0.00")
        for value, expected_code in (
            ("12.345", "INVALID_DECIMAL"),
            ("007.50", "INVALID_DECIMAL"),
            ("1e3", "INVALID_DECIMAL"),
            ("+1.50", "INVALID_DECIMAL"),
            ("-0.00", "INVALID_DECIMAL"),
            ("NaN", "INVALID_DECIMAL"),
            ("1234567890123456789.00", "INVALID_DECIMAL"),
            ("1000.01", "INVALID_RANGE"),
        ):
            with self.subTest(value=value):
                with self.assertRaises(SpecificationCodecError) as raised:
                    normalize_specification_value(money, value)
                self.assertEqual(issue(raised.exception).code, expected_code)

    def test_decimal_does_not_accept_numbers(self):
        money = field("cost", "decimal", validation_data=validation(scale=2))
        for value in (12, 12.0, True):
            with self.subTest(value=repr(value)):
                with self.assertRaises(SpecificationCodecError) as raised:
                    normalize_specification_value(money, value)
                self.assertEqual(issue(raised.exception).code, "INVALID_TYPE")

    def test_boolean_false_is_a_present_value_and_coercion_is_rejected(self):
        approved = field("approved", "boolean", required=True)
        self.assertIs(normalize_specification_value(approved, False), False)
        for value in (0, 1, "false", "true"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(SpecificationCodecError) as raised:
                    normalize_specification_value(approved, value)
                self.assertEqual(issue(raised.exception).code, "INVALID_TYPE")

    def test_date_requires_canonical_real_calendar_date(self):
        purchase_date = field("purchase_date", "date")
        self.assertEqual(normalize_specification_value(purchase_date, "2024-02-29"), "2024-02-29")
        for value in ("2024-02-30", "2024-2-3", "2024-01-01T00:00:00", ""):
            with self.subTest(value=value):
                with self.assertRaises(SpecificationCodecError) as raised:
                    normalize_specification_value(purchase_date, value)
                self.assertEqual(issue(raised.exception).code, "INVALID_DATE")

    def test_select_values_use_keys_and_multiselect_storage_is_key_sorted(self):
        choices = choice_set(active=("a", "b", "c"), deprecated=("old",))
        single = field("status", "single_select", choice_set=choices)
        multi = field("tags", "multi_select", choice_set=choices, validation_data=validation(max_values=3))
        self.assertEqual(normalize_specification_value(single, "b"), "b")
        self.assertEqual(normalize_specification_value(multi, ["c", "a"]), ("a", "c"))
        with self.assertRaises(SpecificationCodecError) as raised:
            normalize_specification_value(multi, ["b", "a", "b"])
        self.assertEqual(issue(raised.exception).code, "INVALID_CHOICE")
        with self.assertRaises(SpecificationCodecError) as raised:
            normalize_specification_value(single, "label-for-b")
        self.assertEqual(issue(raised.exception).code, "INVALID_CHOICE")

    def test_multiselect_display_order_uses_choice_position_without_rewriting_storage_order(self):
        choices = choice_set(active=("zulu", "alpha"))
        tags = field("tags", "multi_select", choice_set=choices, validation_data=validation(max_values=2))
        stored = normalize_specification_value(tags, ["alpha", "zulu"])
        self.assertEqual(stored, ("alpha", "zulu"))
        self.assertEqual(order_multiselect_for_display(tags, stored), ("zulu", "alpha"))

    def test_deprecated_choice_is_only_retained_from_the_original_field_value(self):
        choices = choice_set(active=("active_x", "active_y"), deprecated=("old_tag_a", "old_tag_b"))
        tags = field("tags", "multi_select", choice_set=choices, validation_data=validation(max_values=4))
        self.assertEqual(
            normalize_specification_value(
                tags,
                ["active_y", "active_x", "old_tag_b"],
                original_value=["active_x", "old_tag_a", "old_tag_b"],
            ),
            ("active_x", "active_y", "old_tag_b"),
        )
        with self.assertRaises(SpecificationCodecError) as raised:
            normalize_specification_value(
                tags,
                ["active_x", "old_tag_a"],
                original_value=["active_x", "old_tag_b"],
            )
        self.assertEqual(issue(raised.exception).code, "INVALID_CHOICE")

    def test_deprecated_field_is_read_only_even_for_an_unchanged_setter(self):
        retired = field("legacy", "text", lifecycle="deprecated")
        with self.assertRaises(SpecificationCodecError) as raised:
            normalize_specification_value(retired, "same")
        self.assertEqual(issue(raised.exception).code, "READ_ONLY_FIELD")


class SpecificationPatchTests(unittest.TestCase):
    def setUp(self):
        self.fields = {
            FieldKey("serial"): field("serial", "text"),
            FieldKey("owner_note"): field("owner_note", "text"),
            FieldKey("active"): field("active", "boolean", nullable=True),
            FieldKey("quantity"): field("quantity", "integer"),
            FieldKey("required_text"): field("required_text", "text", required=True),
            FieldKey("required_tags"): field(
                "required_tags",
                "multi_select",
                required=True,
                choice_set=choice_set(active=("a", "b")),
                validation_data=validation(max_values=2),
            ),
            FieldKey("cost"): field("cost", "decimal", validation_data=validation(scale=2)),
        }

    def test_duplicate_setters_are_rejected_before_mapping_and_do_not_mutate_storage(self):
        initial = {"serial": "ABC-1"}
        with self.assertRaises(SpecificationCodecError) as raised:
            normalize_specification_patch(
                self.fields,
                initial,
                setters=(("serial", "ABC-2"), ("serial", "ABC-3")),
                operation="value_edit",
            )
        self.assertEqual(issue(raised.exception).code, "DUPLICATE_FIELD")
        self.assertEqual(issue(raised.exception).path, ("set",))
        self.assertEqual(initial, {"serial": "ABC-1"})

    def test_set_clear_overlap_is_rejected_as_one_atomic_operation(self):
        initial = {"serial": "ABC-1"}
        with self.assertRaises(SpecificationCodecError) as raised:
            normalize_specification_patch(
                self.fields,
                initial,
                setters=(("serial", "ABC-2"),),
                clear_keys=("serial",),
                operation="value_edit",
            )
        self.assertEqual(issue(raised.exception).code, "CONFLICT_CLEAR_OVERLAP")
        self.assertEqual(issue(raised.exception).path, ())
        self.assertEqual(initial, {"serial": "ABC-1"})

    def test_unknown_key_rejects_valid_siblings_without_partial_result(self):
        initial = {"serial": "ABC-1"}
        with self.assertRaises(SpecificationCodecError) as raised:
            normalize_specification_patch(
                self.fields,
                initial,
                setters=(("serial", "ABC-2"), ("unknown_key", "x")),
                operation="value_edit",
            )
        self.assertEqual(issue(raised.exception).code, "UNKNOWN_FIELD_KEY")
        self.assertEqual(issue(raised.exception).path, ("set", "unknown_key"))
        self.assertEqual(initial, {"serial": "ABC-1"})

    def test_clear_removes_a_key_and_absent_keys_are_preserved_byte_for_value(self):
        invalid_history = {"cost": "not-a-decimal"}
        initial = {
            "serial": "ABC-1",
            "active": False,
            "quantity": 0,
            "owner_note": "",
            "nullable_missing": None,
            **invalid_history,
        }
        result = normalize_specification_patch(
            self.fields,
            initial,
            setters=(("owner_note", "  keep whitespace  "),),
            clear_keys=("serial",),
            operation="value_edit",
            validate_required=False,
        )
        self.assertNotIn("serial", result.stored_values)
        self.assertEqual(result.stored_values["active"], False)
        self.assertEqual(result.stored_values["quantity"], 0)
        self.assertEqual(result.stored_values["nullable_missing"], None)
        self.assertEqual(result.stored_values["cost"], "not-a-decimal")
        self.assertEqual(result.set_values, {"owner_note": "  keep whitespace  "})
        self.assertEqual(result.clear_keys, ("serial",))
        self.assertEqual(initial["serial"], "ABC-1")

    def test_optional_null_empty_false_zero_and_empty_list_are_distinct(self):
        fields = {
            FieldKey("note"): field("note", "text", nullable=True),
            FieldKey("active"): field("active", "boolean", nullable=True),
            FieldKey("quantity"): field("quantity", "integer"),
            FieldKey("tags"): field(
                "tags",
                "multi_select",
                choice_set=choice_set(active=("a", "b")),
                validation_data=validation(max_values=2),
            ),
        }
        result = normalize_specification_patch(
            fields,
            {},
            setters=(("note", ""), ("active", None), ("quantity", 0), ("tags", [])),
            operation="value_edit",
        )
        self.assertEqual(result.stored_values, {"note": "", "active": None, "quantity": 0, "tags": ()})
        self.assertNotIn("missing", result.stored_values)

    def test_requiredness_is_operation_specific_and_false_zero_satisfy_presence(self):
        initial = {"active": False, "quantity": 0}
        fields = {
            FieldKey("active"): field("active", "boolean", required=True),
            FieldKey("quantity"): field("quantity", "integer", required=True),
            FieldKey("required_text"): self.fields[FieldKey("required_text")],
        }
        native_result = normalize_specification_patch(
            fields,
            initial,
            setters=(("active", False),),
            operation="native_edit",
        )
        self.assertEqual(native_result.stored_values, initial)
        with self.assertRaises(SpecificationCodecError) as raised:
            normalize_specification_patch(fields, initial, operation="value_edit")
        self.assertEqual(issue(raised.exception).code, "REQUIRED_FIELD")
        self.assertEqual(issue(raised.exception).field_key, FieldKey("required_text"))

    def test_null_setters_and_clear_containers_are_not_treated_as_omitted(self):
        for kwargs in ({"setters": None}, {"clear_keys": None}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(SpecificationCodecError) as raised:
                    normalize_specification_patch(self.fields, {}, operation="native_edit", **kwargs)
                self.assertEqual(issue(raised.exception).code, "INVALID_TYPE")

    def test_required_empty_multiselect_is_rejected_but_nonempty_is_valid(self):
        with self.assertRaises(SpecificationCodecError) as raised:
            normalize_specification_patch(
                self.fields,
                {},
                setters=(("required_tags", []),),
                operation="value_edit",
            )
        self.assertEqual(issue(raised.exception).code, "REQUIRED_FIELD")
        result = normalize_specification_patch(
            self.fields,
            {},
            setters=(("required_tags", ["a", "b"]),),
            operation="value_edit",
            validate_required=False,
        )
        self.assertEqual(result.stored_values["required_tags"], ("a", "b"))

    def test_removed_deprecated_choice_cannot_be_reintroduced(self):
        history = json.loads(HISTORY_FIXTURE_PATH.read_text(encoding="utf-8"))
        case = next(item for item in history["cases"] if item["id"] == "T02-HIST-004")
        choices = choice_set(active=("active_x", "active_y"), deprecated=("old_tag_a", "old_tag_b"))
        fields = {
            FieldKey("tags"): field(
                "tags",
                "multi_select",
                choice_set=choices,
                validation_data=validation(max_values=4),
            )
        }
        first = normalize_specification_patch(
            fields,
            case["initial_state"]["before_removal"],
            setters=tuple(case["operation"]["steps"][0]["set"].items()),
            operation="value_edit",
        )
        self.assertEqual(json_value(first.stored_values), case["expected_result"]["removal_result"]["stored"])
        with self.assertRaises(SpecificationCodecError) as raised:
            normalize_specification_patch(
                fields,
                first.stored_values,
                setters=tuple(case["operation"]["steps"][1]["set"].items()),
                operation="value_edit",
            )
        self.assertEqual(issue(raised.exception).code, "INVALID_CHOICE")
        self.assertEqual(first.stored_values, {"tags": ("active_x", "old_tag_b")})


class T02ScalarFixtureConsumerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.fields = {}
        for key, raw in cls.document["context"]["fields"].items():
            definition_validation = validation(
                minimum=str(raw["min"]) if "min" in raw else None,
                maximum=str(raw["max"]) if "max" in raw else None,
                scale=raw.get("scale"),
                max_length=raw.get("max_length"),
                max_values=raw.get("max_values"),
            )
            choices = raw.get("choice_set")
            choice_definition = None
            if choices is not None:
                choice_definition = choice_set(
                    active=tuple(choices.get("active", ())),
                    deprecated=tuple(choices.get("deprecated", ())),
                )
            elif key in {"tags", "required_tags"}:
                choice_definition = choice_set(active=("a", "b"))
            cls.fields[FieldKey(key)] = field(
                key,
                raw["type"],
                required=raw.get("required", False),
                nullable=raw.get("nullable", False),
                choice_set=choice_definition,
                validation_data=definition_validation,
            )

    def test_all_t02_scalar_cases_reach_the_pure_patch_boundary(self):
        for case in self.document["cases"]:
            with self.subTest(case=case["id"]):
                operation = case["operation"]
                setters = tuple(operation.get("set", {}).items())
                if "set_list" in operation:
                    setters = tuple(tuple(item) for item in operation["set_list"])
                try:
                    result = normalize_specification_patch(
                        self.fields,
                        case["initial_state"],
                        setters=setters,
                        clear_keys=tuple(operation.get("clear", ())),
                        operation="value_edit",
                        validate_required=False,
                    )
                except SpecificationCodecError as raised:
                    expected = case["expected_result"]
                    self.assertEqual(expected["outcome"], "rejected")
                    self.assertEqual(issue(raised).code, expected["error"])
                    if "path" in expected:
                        self.assertEqual(list(issue(raised).path), expected["path"])
                else:
                    expected = case["expected_result"]
                    self.assertEqual(expected["outcome"], "accepted")
                    self.assertEqual(json_value(result.stored_values), expected["stored"])
                    unchanged = {
                        key: result.stored_values[key]
                        for key, value in case["expected_unchanged"].items()
                        if value != "removed" and key in result.stored_values
                    }
                    self.assertEqual(
                        json_value(unchanged),
                        {key: value for key, value in case["expected_unchanged"].items() if value != "removed"},
                    )


if __name__ == "__main__":
    unittest.main()
