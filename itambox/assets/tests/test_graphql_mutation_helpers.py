"""Branch coverage for the pure mutation input helpers.

These helpers guard every GraphQL mutation input path; the tests exercise the
rejection branches that the happy-path suites leave uncovered.
"""

from decimal import Decimal

from django.test import SimpleTestCase

from assets.graphql_specifications.mutations import (
    _decimal_or_none,
    _definition_payload,
    _definition_result,
    _enum_value,
    _field,
    _field_type,
    _graphql_error,
    _graphql_path,
    _has_field,
    _identity,
    _identity_list,
    _InputError,
    _issue,
    _message,
    _optional_bool,
    _optional_id,
    _optional_int,
    _optional_string,
    _positive_id,
    _raise_preview_failure,
    _required_string,
    _string_value,
    _targets,
    _validation,
)
from extras.services.definition_command_contracts import DefinitionRejectedDTO


class _Value:
    def __init__(self, value):
        self.value = value


class MutationHelperTests(SimpleTestCase):
    def test_field_reads_mapping_and_attribute(self):
        self.assertEqual(_field({"a": 1}, "a"), 1)
        self.assertEqual(_field(_Value(7), "value"), 7)
        self.assertEqual(_field({}, "missing", default=3), 3)

    def test_has_field_detects_missing(self):
        self.assertTrue(_has_field({"a": 1}, "a"))
        self.assertFalse(_has_field({}, "a"))

    def test_message_known_and_unknown_keys(self):
        self.assertTrue(_message("specifications.invalid_type"))
        self.assertEqual(_message("specifications.stale_resource"), "The resource changed after the plan was created.")

    def test_graphql_error_paths_with_and_without_prefix(self):
        err = _graphql_error(_issue("INVALID_TYPE", path=("input", "x")))
        self.assertEqual(err.extensions["code"], "INVALID_TYPE")
        prefixed = _graphql_error(_issue("INVALID_TYPE", path=("x",)), prefix_input=True)
        self.assertEqual(prefixed.extensions["path"], ["input", "x"])

    def test_graphql_path_prefix_handling(self):
        self.assertEqual(_graphql_path(_issue("X", path=("input", "a")), prefix_input=True), ("input", "a"))
        self.assertEqual(_graphql_path(_issue("X", path=("a",)), prefix_input=True), ("input", "a"))

    def test_raise_preview_failure_with_and_without_issues(self):
        with self.assertRaises(Exception):
            _raise_preview_failure((_issue("STALE_RESOURCE"),))
        with self.assertRaises(Exception):
            _raise_preview_failure(())

    def test_identity_rejections(self):
        for bad in (5, "no-slash", "a/b/c", "/local", "namespace/"):
            with self.assertRaises(_InputError) as ctx:
                _identity(bad, path=("input", "fields", "identity"))
            self.assertEqual(ctx.exception.issues[0].code, "INVALID_TYPE")
        self.assertEqual(_identity("ns/local", path=("x",)), "ns/local")

    def test_identity_list_rejections(self):
        with self.assertRaises(_InputError):
            _identity_list("not-a-list", path=("x",))
        with self.assertRaises(_InputError) as ctx:
            _identity_list(["ns/a", "ns/a"], path=("x",))
        self.assertEqual(ctx.exception.issues[0].code, "DUPLICATE_FIELD")
        self.assertEqual(_identity_list(["ns/a", "ns/b"], path=("x",)), ("ns/a", "ns/b"))

    def test_string_value_rejections(self):
        self.assertEqual(_string_value("ok", path=("x",)), "ok")
        self.assertEqual(_string_value("", path=("x",)), "")
        for bad in (5, ""):
            with self.assertRaises(_InputError):
                _string_value(bad, path=("x",), allow_empty=False)

    def test_optional_bool_rejections(self):
        self.assertIsNone(_optional_bool(None, path=("x",)))
        self.assertIs(_optional_bool(True, path=("x",)), True)
        self.assertIs(_optional_bool(False, path=("x",)), False)
        for bad in ("yes", 1):
            with self.assertRaises(_InputError):
                _optional_bool(bad, path=("x",))

    def test_optional_int_rejections(self):
        self.assertIsNone(_optional_int(None, path=("x",)))
        self.assertEqual(_optional_int(3, path=("x",)), 3)
        with self.assertRaises(_InputError):
            _optional_int("3", path=("x",))

    def test_targets_rejections_and_mapping(self):
        self.assertEqual(
            _targets({"targets": [_Value("asset_type"), _Value("asset")]}), ("assets.assettype", "assets.asset")
        )
        with self.assertRaises(_InputError):
            _targets({"targets": "asset_type"})
        with self.assertRaises(_InputError) as ctx:
            _targets({"targets": [_Value("asset_type"), _Value("asset_type")]})
        self.assertEqual(ctx.exception.issues[0].code, "DUPLICATE_FIELD")
        with self.assertRaises(_InputError):
            _targets({"targets": [_Value("bogus")]})

    def test_field_type_rejections(self):
        self.assertEqual(_field_type(_Value("text")), "text")
        self.assertEqual(_field_type(_Value("single_select")), "single-select")
        with self.assertRaises(_InputError):
            _field_type(_Value("unknown_type"))

    def test_decimal_or_none(self):
        self.assertIsNone(_decimal_or_none(None, path=("x",)))
        self.assertEqual(_decimal_or_none("1.5", path=("x",)), Decimal("1.5"))
        with self.assertRaises(_InputError):
            _decimal_or_none("not-a-number", path=("x",))

    def test_validation_missing_and_full(self):
        with self.assertRaises(_InputError):
            _validation(None)
        result = _validation(
            {
                "minimum": "1.5",
                "maximum": "9.5",
                "scale": 2,
                "maxLength": 12,
                "max_values": 3,
                "regex": "^x",
                "rule": "r",
            }
        )
        self.assertEqual(result["minimum_value"], Decimal("1.5"))
        self.assertEqual(result["decimal_scale"], 2)
        self.assertEqual(result["max_values"], 3)

    def test_definition_payload_branches(self):
        rejected = _definition_payload(
            DefinitionRejectedDTO(
                outcome="rejected",
                definition_kind=None,
                definition_id=None,
                identity=None,
                issues=(_issue("STALE_RESOURCE"),),
            )
        )
        self.assertIsNone(rejected.field)
        self.assertEqual(rejected.user_errors[0].code, "STALE_RESOURCE")
        unavailable = _definition_payload(object())
        self.assertEqual(unavailable.user_errors[0].code, "OBJECT_UNAVAILABLE")

    def test_definition_result_catches_value_errors(self):
        def boom():
            raise ValueError("bad identity")

        result = _definition_result(boom)
        self.assertIsInstance(result, DefinitionRejectedDTO)
        self.assertEqual(result.issues[0].code, "INVALID_TYPE")

    def test_positive_id_forms(self):
        self.assertEqual(_positive_id(4, path=("x",)), 4)
        self.assertEqual(_positive_id("4", path=("x",)), 4)
        for bad in (0, -1, "0", "4.5", "abc"):
            with self.assertRaises(_InputError):
                _positive_id(bad, path=("x",))

    def test_optional_id_and_required_string(self):
        self.assertIsNone(_optional_id(None, path=("x",)))
        self.assertEqual(_optional_id(2, path=("x",)), 2)
        self.assertEqual(_required_string("name", path=("x",)), "name")
        for bad in ("", 5):
            with self.assertRaises(_InputError):
                _required_string(bad, path=("x",))

    def test_impact_token_guard_rejects_explicit_values(self):
        from assets.graphql_specifications.mutations import _MISSING, _impact_token_guard

        _impact_token_guard(_MISSING, path=("x",))
        _impact_token_guard(None, path=("x",))
        with self.assertRaises(_InputError) as ctx:
            _impact_token_guard("legacy", path=("x",))
        self.assertEqual(ctx.exception.issues[0].code, "UNSUPPORTED_STRUCTURE")

    def test_lifecycle_rejects_unknown_values(self):
        from assets.graphql_specifications.mutations import _MISSING, _lifecycle

        self.assertIsNone(_lifecycle(_MISSING, path=("x",)))
        self.assertIsNone(_lifecycle(None, path=("x",)))
        self.assertEqual(_lifecycle(_Value("deprecated"), path=("x",)), "deprecated")
        with self.assertRaises(_InputError) as ctx:
            _lifecycle(_Value("inactive"), path=("x",))
        self.assertEqual(ctx.exception.issues[0].code, "INVALID_TYPE")

    def test_positive_id_string_decimal_and_rejections(self):
        self.assertEqual(_positive_id("42", path=("x",)), 42)
        for bad in (0, -1, "0", "-3", "12.5", "abc", "üñí", 2.5):
            with self.assertRaises(_InputError) as ctx:
                _positive_id(bad, path=("x",))
            self.assertEqual(ctx.exception.issues[0].code, "INVALID_TYPE")

    def test_graphql_path_stale_and_explicit_prefix(self):
        self.assertEqual(
            _graphql_path(_issue("STALE_RESOURCE"), prefix_input=True),
            ("input", "expectedResourceRevision"),
        )
        self.assertEqual(
            _graphql_path(_issue("STALE_RESOURCE"), prefix_input=False),
            ("expectedResourceRevision",),
        )

    def test_optional_string_and_enum_value(self):
        self.assertIsNone(_optional_string(None, path=("x",)))
        self.assertEqual(_optional_string("v", path=("x",)), "v")
        self.assertEqual(_enum_value(_Value("asset_type")), "asset_type")
        self.assertEqual(_enum_value("plain"), "plain")
