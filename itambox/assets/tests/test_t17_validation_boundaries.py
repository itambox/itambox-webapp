"""Boundary branches for the side-effect-free Type Library validation helpers."""

from decimal import Decimal

import pytest
from django.test import SimpleTestCase

from assets.services.type_library_validation.errors import LibraryValidationError
from assets.services.type_library_validation.limits import ValidationLimits
from assets.services.type_library_validation.validation import (
    _NAMESPACE_RE,
    InstalledDependency,
    ValidatedLibraryDocument,
    _catalog_identity,
    _check_generic_value,
    _check_pattern,
    _check_properties,
    _coerce_installed_dependencies,
    _decimal,
    _expect_array,
    _expect_bool,
    _expect_enum_array,
    _expect_int,
    _expect_object,
    _expect_string,
    _owned_identity,
    _qualified_identity,
    _validate_field_identity,
    _validate_multi_value_shape,
    _validate_safe_regex,
    _ValidationContext,
)


def _code(excinfo) -> str:
    issues = getattr(excinfo.value, "issues", ())
    return issues[0].code if issues else "?"


class TestExpectationHelpers:
    def test_expect_object(self):
        assert _expect_object({"a": 1}, ("x",)) == {"a": 1}
        with pytest.raises(LibraryValidationError) as e:
            _expect_object([], ("x",))
        assert _code(e) == "SCHEMA_TYPE"

    def test_expect_array(self):
        assert _expect_array([1], ("x",)) == [1]
        with pytest.raises(LibraryValidationError) as e:
            _expect_array({}, ("x",))
        assert _code(e) == "SCHEMA_TYPE"

    def test_expect_string(self):
        assert _expect_string("ok", ("x",)) == "ok"
        with pytest.raises(LibraryValidationError):
            _expect_string(5, ("x",))
        with pytest.raises(LibraryValidationError):
            _expect_string("", ("x",), nonempty=True)
        with pytest.raises(LibraryValidationError) as e:
            _expect_string("a\x00b", ("x",))
        assert _code(e) == "INVALID_TYPE"

    def test_expect_bool_and_int(self):
        assert _expect_bool(True, ("x",)) is True
        with pytest.raises(LibraryValidationError):
            _expect_bool(1, ("x",))
        assert _expect_int(2, ("x",)) == 2
        with pytest.raises(LibraryValidationError):
            _expect_int("2", ("x",))
        with pytest.raises(LibraryValidationError) as e:
            _expect_int(2, ("x",), minimum=3)
        assert _code(e) == "INVALID_RANGE"
        with pytest.raises(LibraryValidationError):
            _expect_int(9, ("x",), maximum=8)

    def test_expect_enum_array(self):
        assert _expect_enum_array(
            ["a", "b"], ("x",), allowed=frozenset({"a", "b"}), minimum=1, maximum=2, label="L"
        ) == ["a", "b"]
        with pytest.raises(LibraryValidationError):
            _expect_enum_array([], ("x",), allowed=frozenset({"a"}), minimum=1, maximum=2, label="L")
        with pytest.raises(LibraryValidationError):
            _expect_enum_array(
                ["a", "b", "c"], ("x",), allowed=frozenset({"a", "b", "c"}), minimum=1, maximum=2, label="L"
            )
        with pytest.raises(LibraryValidationError) as e:
            _expect_enum_array([1], ("x",), allowed=frozenset({"a"}), minimum=1, maximum=2, label="L")
        assert _code(e) == "SCHEMA_TYPE"
        with pytest.raises(LibraryValidationError):
            _expect_enum_array(["nope"], ("x",), allowed=frozenset({"a"}), minimum=1, maximum=2, label="L")
        with pytest.raises(LibraryValidationError) as e:
            _expect_enum_array(["a", "a"], ("x",), allowed=frozenset({"a"}), minimum=1, maximum=2, label="L")
        assert _code(e) == "DUPLICATE_IDENTITY"

    def test_check_properties(self):
        assert _check_properties({"a": 1}, ("x",), required=("a",), optional=("b",)) == {"a": 1}
        with pytest.raises(LibraryValidationError) as e:
            _check_properties({"z": 1}, ("x",), required=("a",), optional=("b",))
        assert _code(e) == "UNKNOWN_PROPERTY"
        with pytest.raises(LibraryValidationError) as e:
            _check_properties({}, ("x",), required=("a",), optional=("b",))
        assert _code(e) == "MISSING_PROPERTY"

    def test_check_pattern(self):
        assert _check_pattern("acme", _NAMESPACE_RE, ("x",), "bad") == "acme"
        with pytest.raises(LibraryValidationError) as e:
            _check_pattern("NO", _NAMESPACE_RE, ("x",), "bad")
        assert _code(e) == "INVALID_IDENTITY"


class TestIdentityHelpers:
    def test_qualified_identity(self):
        assert _qualified_identity("ns/local", ("x",)) == "ns/local"
        with pytest.raises(LibraryValidationError):
            _qualified_identity("no-slash", ("x",))

    def test_owned_identity(self):
        assert _owned_identity("acme/field", ("x",), "acme") == "acme/field"
        assert _owned_identity("acme/field", ("x",), frozenset({"acme", "other"})) == "acme/field"
        with pytest.raises(LibraryValidationError) as e:
            _owned_identity("other/field", ("x",), "acme")
        assert _code(e) == "NAMESPACE_TAKEOVER"
        with pytest.raises(LibraryValidationError) as e:
            _owned_identity("catalog/field", ("x",), None)
        assert _code(e) == "NAMESPACE_TAKEOVER"

    def test_catalog_identity(self):
        assert _catalog_identity("catalog/laptops", ("x",)) == "catalog/laptops"
        with pytest.raises(LibraryValidationError):
            _catalog_identity("acme/laptops", ("x",))

    def test_validate_safe_regex(self):
        _validate_safe_regex("^[a-z]+$", ("x",))
        with pytest.raises(LibraryValidationError):
            _validate_safe_regex("x" * 300, ("x",))
        with pytest.raises(LibraryValidationError):
            _validate_safe_regex("(?i)abc", ("x",))
        with pytest.raises(LibraryValidationError):
            _validate_safe_regex(r"(a)\1", ("x",))
        with pytest.raises(LibraryValidationError):
            _validate_safe_regex("a**b", ("x",))

    def test_decimal(self):
        assert _decimal("1.5", ("x",)) == Decimal("1.5")
        with pytest.raises(LibraryValidationError):
            _decimal("1e5", ("x",))
        with pytest.raises(LibraryValidationError):
            _decimal("-0", ("x",))


class TestShapeHelpers:
    def test_multi_value_shape(self):
        _validate_multi_value_shape(["a", "b"], ("x",))
        with pytest.raises(LibraryValidationError) as e:
            _validate_multi_value_shape(["a"] * 65, ("x",))
        assert _code(e) == "RESOURCE_LIMIT"
        with pytest.raises(LibraryValidationError):
            _validate_multi_value_shape([1], ("x",))
        with pytest.raises(LibraryValidationError):
            _validate_multi_value_shape(["a", "a"], ("x",))

    def test_generic_value(self):
        limits = ValidationLimits()
        _check_generic_value("ok", ("x",), limits)
        with pytest.raises(LibraryValidationError):
            _check_generic_value("x" * 4097, ("x",), limits)
        _check_generic_value(5, ("x",), limits)
        with pytest.raises(LibraryValidationError):
            _check_generic_value(10**20, ("x",), limits)
        _check_generic_value(True, ("x",), limits)
        _check_generic_value(None, ("x",), limits)
        _check_generic_value(["a"], ("x",), limits)
        with pytest.raises(LibraryValidationError) as e:
            _check_generic_value({"nested": 1}, ("x",), limits)
        assert _code(e) == "UNSUPPORTED_STRUCTURE"


class TestFieldIdentity:
    def test_field_identity(self):
        _validate_field_identity({"key": "serial", "namespace": "itambox"}, ("x",), None)
        _validate_field_identity({"key": "acme__serial", "namespace": "acme"}, ("x",), "acme")
        with pytest.raises(LibraryValidationError) as e:
            _validate_field_identity({"key": "serial", "namespace": "other"}, ("x",), "acme")
        assert _code(e) == "NAMESPACE_TAKEOVER"
        with pytest.raises(LibraryValidationError):
            _validate_field_identity({"key": "serial", "namespace": "catalog"}, ("x",), None)
        with pytest.raises(LibraryValidationError) as e:
            _validate_field_identity({"key": "serial", "namespace": "acme"}, ("x",), None)
        assert _code(e) == "INVALID_IDENTITY"


class TestDependencyCoercion:
    def test_coercion_forms(self):
        assert _coerce_installed_dependencies(None) == ()
        assert _coerce_installed_dependencies([InstalledDependency("acme", 1, "sha256:" + "0" * 64)]) == (
            InstalledDependency("acme", 1, "sha256:" + "0" * 64),
        )
        coerced = _coerce_installed_dependencies({("acme", 1): "sha256:" + "0" * 64})
        assert coerced == (InstalledDependency("acme", 1, "sha256:" + "0" * 64),)
        coerced = _coerce_installed_dependencies(
            {"acme": {"release": 1, "digest": "sha256:" + "0" * 64, "document": {"kind": "library", "fields": {}}}}
        )
        assert coerced[0].document == {"kind": "library", "fields": {}}
        with pytest.raises(TypeError):
            _coerce_installed_dependencies({"acme": 5})
        with pytest.raises(TypeError):
            _coerce_installed_dependencies({5: "x"})
        with pytest.raises(TypeError):
            _coerce_installed_dependencies({"acme": {"release": "x", "digest": 5}})

    def test_context_rejects_duplicate_dependencies(self):
        dep = InstalledDependency("acme", 1, "sha256:" + "0" * 64)
        with pytest.raises(LibraryValidationError) as e:
            _ValidationContext(ValidationLimits(), [dep, dep])
        assert _code(e) == "DUPLICATE_DEPENDENCY"

    def test_context_count_limit(self):
        ctx = _ValidationContext(ValidationLimits(), [])
        ctx.count("fields", 1, ("x",))
        limits = ValidationLimits()
        if limits.max_fields:
            ctx = _ValidationContext(limits, [])
            with pytest.raises(LibraryValidationError) as e:
                ctx.count("fields", limits.max_fields + 1, ("x",))
            assert _code(e) == "RESOURCE_LIMIT"


class TestDtos:
    def test_validated_document_digest(self):
        doc = ValidatedLibraryDocument(
            kind="library",
            normalized_document={"fields": {}},
            canonical_bytes=b"{}",
            semantic_digest="sha256:" + "0" * 64,
        )
        assert doc.digest == doc.semantic_digest


class TestIdentityGuards(SimpleTestCase):
    def test_owned_identity_rejects_foreign_namespace(self):
        from assets.services.type_library_validation.validation import _owned_identity

        self.assertEqual(_owned_identity("acme/local", ("x",), "acme"), "acme/local")
        self.assertEqual(_owned_identity("acme/local", ("x",), frozenset({"acme", "corp"})), "acme/local")
        with self.assertRaises(LibraryValidationError):
            _owned_identity("corp/local", ("x",), "acme")
        with self.assertRaises(LibraryValidationError):
            _owned_identity("catalog/local", ("x",), None)

    def test_catalog_identity_enforces_namespace(self):
        from assets.services.type_library_validation.validation import _catalog_identity

        self.assertEqual(_catalog_identity("catalog/local", ("x",)), "catalog/local")
        with self.assertRaises(LibraryValidationError):
            _catalog_identity("acme/local", ("x",))

    def test_safe_regex_rejections(self):
        from assets.services.type_library_validation.validation import _validate_safe_regex

        _validate_safe_regex(r"^[a-z]+$", ("x",))
        for bad in ("a" * 257, "(?i:abc)", r"\1", r"(?P=name)", r"*ab*cd*", "((("):
            with self.assertRaises(LibraryValidationError):
                _validate_safe_regex(bad, ("x",))

    def test_decimal_bound_rejections(self):
        from assets.services.type_library_validation.validation import _decimal

        self.assertEqual(_decimal("1.25", ("x",)), Decimal("1.25"))
        for bad in ("-0", "-0.000000", "1.2345678", "abc", "1e3"):
            with self.assertRaises(LibraryValidationError):
                _decimal(bad, ("x",))

    def test_multi_value_shape_rejections(self):
        from assets.services.type_library_validation.validation import _validate_multi_value_shape

        _validate_multi_value_shape(["a", "b"], ("x",))
        with self.assertRaises(LibraryValidationError):
            _validate_multi_value_shape(["a"] * 65, ("x",))
        with self.assertRaises(LibraryValidationError):
            _validate_multi_value_shape(["a", 5], ("x",))
        with self.assertRaises(LibraryValidationError):
            _validate_multi_value_shape(["a", "a"], ("x",))
        with self.assertRaises(LibraryValidationError):
            _validate_multi_value_shape(["a-b"], ("x",))

    def test_generic_value_limits(self):
        from assets.services.type_library_validation.validation import _check_generic_value

        _check_generic_value("x" * 4096, ("x",), None)
        _check_generic_value(5, ("x",), None)
        _check_generic_value(True, ("x",), None)
        _check_generic_value(None, ("x",), None)
        _check_generic_value(["a"], ("x",), None)
        with self.assertRaises(LibraryValidationError):
            _check_generic_value("x" * 4097, ("x",), None)
        with self.assertRaises(LibraryValidationError):
            _check_generic_value(9007199254740992, ("x",), None)
        with self.assertRaises(LibraryValidationError):
            _check_generic_value({"nested": 1}, ("x",), None)

    def test_field_identity_prefix_enforcement(self):
        from assets.services.type_library_validation.validation import _validate_field_identity

        _validate_field_identity({"key": "itambox_core", "namespace": "itambox"}, ("x",), None)
        _validate_field_identity({"key": "acme__serial", "namespace": "acme"}, ("x",), "acme")
        with self.assertRaises(LibraryValidationError):
            _validate_field_identity({"key": "acme__serial", "namespace": "corp"}, ("x",), "acme")
        with self.assertRaises(LibraryValidationError):
            _validate_field_identity({"key": "serial", "namespace": "acme"}, ("x",), None)
        with self.assertRaises(LibraryValidationError):
            _validate_field_identity({"key": "acme__", "namespace": "acme"}, ("x",), None)
        with self.assertRaises(LibraryValidationError):
            _validate_field_identity({"key": "x", "namespace": "catalog"}, ("x",), None)

    def test_field_surface_rejections(self):
        from assets.services.type_library_validation.validation import _validate_field_surface

        base = dict(
            label="Serial",
            help_text="",
            targets=["asset"],
            activation="composed",
            field_type="text",
            required=False,
            nullable=True,
            lifecycle="active",
        )
        self.assertEqual(_validate_field_surface(base, ("x",)), "text")
        with self.assertRaises(LibraryValidationError):
            _validate_field_surface({**base, "label": "L" * 201}, ("x",))
        with self.assertRaises(LibraryValidationError):
            _validate_field_surface({**base, "help_text": "h" * 4097}, ("x",))
        with self.assertRaises(LibraryValidationError):
            _validate_field_surface({**base, "targets": []}, ("x",))
        with self.assertRaises(LibraryValidationError):
            _validate_field_surface({**base, "targets": ["asset", "asset_type", "asset"]}, ("x",))
        with self.assertRaises(LibraryValidationError):
            _validate_field_surface({**base, "activation": "active"}, ("x",))
        with self.assertRaises(LibraryValidationError):
            _validate_field_surface({**base, "field_type": "blob"}, ("x",))
        with self.assertRaises(LibraryValidationError):
            _validate_field_surface({**base, "required": "yes"}, ("x",))
        with self.assertRaises(LibraryValidationError):
            _validate_field_surface({**base, "lifecycle": "ghost"}, ("x",))
