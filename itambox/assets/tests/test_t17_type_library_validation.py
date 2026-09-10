"""DB-free T17 tests for library parsing, graph validation, and normalization."""

from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256

import pytest
import rfc8785

from assets.services.type_library_validation import (
    DependencyReference,
    InstalledDependency,
    LibraryValidationError,
    ValidationLimits,
    validate_library_document,
)


def _release_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "itambox.type-library.release",
        "library": {"namespace": "acme", "release": 1, "label": "Acme"},
        "requires": [],
        "definitions": {
            "choice_sets": [
                {
                    "id": "acme/state",
                    "label": "State",
                    "description": "States",
                    "lifecycle": "active",
                    "choices": [
                        {"key": "on", "label": "On", "lifecycle": "active"},
                        {"key": "off", "label": "Off", "lifecycle": "active"},
                    ],
                }
            ],
            "fields": [
                {
                    "key": "acme__state",
                    "namespace": "acme",
                    "label": "State",
                    "help_text": "",
                    "targets": ["asset_type"],
                    "activation": "composed",
                    "field_type": "multi-select",
                    "required": False,
                    "nullable": False,
                    "lifecycle": "active",
                    "validation": {"max_values": 2},
                    "choice_set": "acme/state",
                },
                {
                    "key": "acme__capacity",
                    "namespace": "acme",
                    "label": "Capacity",
                    "help_text": "",
                    "targets": ["asset_type"],
                    "activation": "composed",
                    "field_type": "decimal",
                    "required": False,
                    "nullable": False,
                    "lifecycle": "active",
                    "validation": {"scale": 3, "minimum": "0.000", "maximum": "99.999"},
                    "quantity_kind": "count",
                    "canonical_unit": None,
                },
            ],
            "fieldsets": [
                {
                    "id": "acme/specs",
                    "label": "Specs",
                    "description": "Specs",
                    "lifecycle": "active",
                    "fields": ["acme/acme__capacity", "acme/acme__state"],
                }
            ],
            "categories": [
                {
                    "id": "catalog/devices",
                    "label": "Devices",
                    "description": "Devices",
                    "lifecycle": "active",
                    "applies_to": ["asset"],
                    "default_fieldsets": ["acme/specs"],
                }
            ],
            "manufacturers": [
                {
                    "id": "catalog/acme",
                    "label": "Acme",
                    "description": "Acme",
                    "lifecycle": "active",
                }
            ],
            "asset_types": [
                {
                    "id": "acme/device-a",
                    "manufacturer": "catalog/acme",
                    "model": "Device",
                    "part_number": "A",
                    "gtin": None,
                    "region": "",
                    "configuration": "",
                    "category": "catalog/devices",
                    "description": "Device",
                    "lifecycle": "active",
                    "fieldsets": ["acme/specs"],
                    "specifications": {"acme__capacity": "1.2", "acme__state": ["on", "off"]},
                    "historical_specifications": {},
                }
            ],
        },
    }


def test_validate_normalizes_domain_before_real_jcs_hashing():
    document = _release_document()
    document["definitions"]["fields"] = list(reversed(document["definitions"]["fields"]))  # type: ignore[index]
    result = validate_library_document(json.dumps(document, ensure_ascii=False).encode("utf-8"))

    assert result.normalized_document["definitions"]["fields"][0]["key"] == "acme__capacity"
    assert result.normalized_document["definitions"]["asset_types"][0]["specifications"]["acme__capacity"] == "1.200"
    assert result.normalized_document["definitions"]["asset_types"][0]["specifications"]["acme__state"] == ["off", "on"]
    assert result.canonical_bytes == rfc8785.dumps(result.normalized_document)
    assert result.semantic_digest == "sha256:" + sha256(result.canonical_bytes).hexdigest()


@pytest.mark.parametrize(
    ("value", "required", "code"),
    (("   ", True, "REQUIRED_FIELD"), ("a\x00b", False, "INVALID_TYPE")),
)
def test_library_text_values_preserve_shared_codec_rejections(value, required, code):
    document = _release_document()
    definition = document["definitions"]["fields"][1]
    definition.update(field_type="text", required=required, validation={"max_length": 50})
    definition.pop("quantity_kind")
    definition.pop("canonical_unit")
    document["definitions"]["asset_types"][0]["specifications"]["acme__capacity"] = value

    with pytest.raises(LibraryValidationError) as caught:
        validate_library_document(json.dumps(document))

    assert caught.value.issues[0].code == code
    assert caught.value.issues[0].path == (
        "definitions",
        "asset_types",
        "acme/device-a",
        "specifications",
        "acme__capacity",
    )


@pytest.mark.parametrize("close_cycle", (False, True))
def test_replacement_walk_handles_long_chains_within_document_limits(close_cycle):
    document = _release_document()
    fields = document["definitions"]["fields"]
    keys = [f"acme__retained_{index:04d}" for index in range(1_100)]
    for index, key in enumerate(keys):
        definition = deepcopy(fields[1])
        definition.update(key=key, lifecycle="deprecated")
        if index + 1 < len(keys):
            definition["replaced_by"] = f"acme/{keys[index + 1]}"
        elif close_cycle:
            definition["replaced_by"] = f"acme/{keys[0]}"
        fields.append(definition)

    if close_cycle:
        with pytest.raises(LibraryValidationError) as caught:
            validate_library_document(json.dumps(document))
        assert caught.value.issues[0].code == "REFERENCE_CYCLE"
    else:
        result = validate_library_document(json.dumps(document))
        assert len(result.normalized_document["definitions"]["fields"]) == len(fields)


@pytest.mark.parametrize("document", (b"\xff" * 17, "\ud800" * 17))
def test_document_size_limit_precedes_unicode_decoding(document):
    with pytest.raises(LibraryValidationError) as caught:
        validate_library_document(document, limits={"max_bytes": 16})
    assert caught.value.issues[0].code == "RESOURCE_LIMIT"


def test_library_metadata_rejects_nul_before_persistence():
    document = _release_document()
    document["library"]["label"] = "Acme\x00label"
    with pytest.raises(LibraryValidationError) as caught:
        validate_library_document(json.dumps(document))
    assert caught.value.issues[0].code == "INVALID_TYPE"
    assert caught.value.issues[0].path == ("library", "label")


def test_duplicate_json_properties_are_rejected_before_overwrite():
    raw = json.dumps(_release_document())[:-1] + ', "definitions": {}}'

    with pytest.raises(LibraryValidationError) as caught:
        validate_library_document(raw)

    assert any(issue.code == "DUPLICATE_PROPERTY" for issue in caught.value.issues)


def test_oversized_json_integer_returns_a_structured_number_error():
    raw = b'{"value":' + b"9" * 4_301 + b"}"

    with pytest.raises(LibraryValidationError) as caught:
        validate_library_document(raw)

    assert caught.value.code == "INVALID_NUMBER"


@pytest.mark.parametrize(
    ("raw", "code"),
    (
        (b"\xff", "INVALID_UTF8"),
        (b'{"value":NaN}', "INVALID_NUMBER"),
        (b"[]", "SCHEMA_TYPE"),
    ),
)
def test_json_parser_rejects_unsafe_wire_values_with_structured_errors(raw: bytes, code: str):
    with pytest.raises(LibraryValidationError) as caught:
        validate_library_document(raw)

    assert caught.value.code == code


def test_json_parser_enforces_size_and_depth_before_schema_validation():
    with pytest.raises(LibraryValidationError) as size_error:
        validate_library_document(b"{}", limits={"max_bytes": 1})
    assert size_error.value.code == "RESOURCE_LIMIT"

    raw = b'{"nested":' + b"[" * 32 + b"0" + b"]" * 32 + b"}"
    with pytest.raises(LibraryValidationError) as depth_error:
        validate_library_document(raw)
    assert depth_error.value.code == "RESOURCE_LIMIT"


def test_unknown_properties_are_rejected():
    document = _release_document()
    document["unexpected"] = True

    with pytest.raises(LibraryValidationError) as caught:
        validate_library_document(json.dumps(document))

    assert any(issue.code == "UNKNOWN_PROPERTY" for issue in caught.value.issues)


def test_invalid_reference_and_global_field_in_fieldset_are_rejected():
    document = _release_document()
    document["definitions"]["fieldsets"][0]["fields"].append("acme/missing")  # type: ignore[index]

    with pytest.raises(LibraryValidationError) as caught:
        validate_library_document(json.dumps(document))

    assert any(issue.code == "INVALID_REFERENCE" for issue in caught.value.issues)


def test_same_kind_choice_replacement_cycles_are_rejected():
    document = _release_document()
    choices = document["definitions"]["choice_sets"][0]["choices"]  # type: ignore[index]
    choices[0]["replaced_by"] = "off"  # type: ignore[index]
    choices[1]["replaced_by"] = "on"  # type: ignore[index]

    with pytest.raises(LibraryValidationError) as caught:
        validate_library_document(json.dumps(document))

    assert caught.value.code == "REFERENCE_CYCLE"


def test_declared_dependency_without_loaded_field_graph_fails_closed():
    document = _release_document()
    digest = "sha256:" + "a" * 64
    document["requires"] = [{"namespace": "core", "release": 1, "digest": digest}]
    document["definitions"]["fieldsets"][0]["fields"].append("core/core__serial")  # type: ignore[index]

    with pytest.raises(LibraryValidationError) as caught:
        validate_library_document(
            json.dumps(document),
            installed_dependencies=[InstalledDependency("core", 1, digest)],
        )

    assert caught.value.code == "DEPENDENCY_GRAPH_UNAVAILABLE"


def test_external_replacement_target_requires_a_loaded_dependency_graph():
    document = _release_document()
    digest = "sha256:" + "b" * 64
    document["requires"] = [{"namespace": "core", "release": 1, "digest": digest}]
    document["definitions"]["fields"][0]["replaced_by"] = "core/core__state"  # type: ignore[index]

    with pytest.raises(LibraryValidationError) as caught:
        validate_library_document(
            json.dumps(document),
            installed_dependencies=[InstalledDependency("core", 1, digest)],
        )

    assert caught.value.code == "DEPENDENCY_GRAPH_UNAVAILABLE"


def test_external_choice_set_without_composition_use_fails_closed():
    document = _release_document()
    digest = "sha256:" + "e" * 64
    document["requires"] = [{"namespace": "core", "release": 1, "digest": digest}]
    document["definitions"]["fields"][0]["choice_set"] = "core/core__state"  # type: ignore[index]
    document["definitions"]["fieldsets"][0]["fields"].remove("acme/acme__state")  # type: ignore[index]
    document["definitions"]["asset_types"][0]["specifications"].pop("acme__state")  # type: ignore[index]

    with pytest.raises(LibraryValidationError) as caught:
        validate_library_document(
            json.dumps(document),
            installed_dependencies=[InstalledDependency("core", 1, digest)],
        )

    assert caught.value.code == "DEPENDENCY_GRAPH_UNAVAILABLE"


def test_external_category_default_fieldset_requires_a_loaded_graph():
    document = _release_document()
    digest = "sha256:" + "f" * 64
    document["requires"] = [{"namespace": "core", "release": 1, "digest": digest}]
    document["definitions"]["categories"][0]["default_fieldsets"] = ["core/core_specs"]  # type: ignore[index]

    with pytest.raises(LibraryValidationError) as caught:
        validate_library_document(
            json.dumps(document),
            installed_dependencies=[InstalledDependency("core", 1, digest)],
        )

    assert caught.value.code == "DEPENDENCY_GRAPH_UNAVAILABLE"


def test_exact_dependency_identity_and_digest_are_required():
    document = _release_document()
    digest = "sha256:" + "c" * 64
    document["requires"] = [{"namespace": "core", "release": 1, "digest": digest}]

    with pytest.raises(LibraryValidationError) as missing:
        validate_library_document(json.dumps(document))
    assert missing.value.code == "DEPENDENCY_MISSING"

    with pytest.raises(LibraryValidationError) as mismatched:
        validate_library_document(
            json.dumps(document),
            installed_dependencies=[InstalledDependency("core", 1, "sha256:" + "d" * 64)],
        )
    assert mismatched.value.code == "DEPENDENCY_DIGEST_MISMATCH"

    validate_library_document(
        json.dumps(document),
        installed_dependencies=[InstalledDependency("core", 1, digest)],
    )


def test_repeated_hardware_structures_are_rejected():
    document = _release_document()
    document["definitions"]["asset_types"][0]["ports"] = []  # type: ignore[index]

    with pytest.raises(LibraryValidationError) as caught:
        validate_library_document(json.dumps(document))

    assert any(issue.code == "UNKNOWN_PROPERTY" for issue in caught.value.issues)


def test_snapshot_counts_upstream_and_effective_definitions_together():
    release = _release_document()
    snapshot = {
        "schema_version": 1,
        "kind": "itambox.type-library.snapshot",
        "upstream": deepcopy(release),
        "effective_definitions": deepcopy(release["definitions"]),
    }
    # The implementation's default limit is deliberately exercised by a focused
    # custom limit so the two halves cannot receive independent allowances.
    with pytest.raises(LibraryValidationError) as caught:
        validate_library_document(json.dumps(snapshot), limits={"max_fields": 2})

    assert any(issue.code == "RESOURCE_LIMIT" for issue in caught.value.issues)


def test_jcs_does_not_use_python_sort_keys_for_utf16_key_ordering():
    document = _release_document()
    # This is a valid Unicode label and is intentionally not normalized.
    document["library"]["label"] = "é"  # type: ignore[index]
    result = validate_library_document(json.dumps(document, ensure_ascii=False))

    assert result.canonical_bytes == rfc8785.dumps(result.normalized_document)
    assert "é".encode("utf-8") in result.canonical_bytes


def test_configured_effective_field_limit_applies_to_the_composed_graph():
    with pytest.raises(LibraryValidationError) as caught:
        validate_library_document(json.dumps(_release_document()), limits={"max_effective_fields_per_type": 1})

    assert any(issue.code == "RESOURCE_LIMIT" for issue in caught.value.issues)


def test_snapshot_allows_a_separate_local_namespace_and_retained_choice_history():
    release = _release_document()
    snapshot = {
        "schema_version": 1,
        "kind": "itambox.type-library.snapshot",
        "upstream": deepcopy(release),
        "effective_definitions": deepcopy(release["definitions"]),
    }
    definitions = snapshot["effective_definitions"]
    definitions["choice_sets"][0]["choices"].append(  # type: ignore[index]
        {"key": "legacy", "label": "Legacy", "lifecycle": "deprecated"}
    )
    definitions["fields"].append(  # type: ignore[index]
        {
            "key": "local__note",
            "namespace": "local",
            "label": "Local note",
            "help_text": "",
            "targets": ["asset_type"],
            "activation": "composed",
            "field_type": "text",
            "required": False,
            "nullable": False,
            "lifecycle": "active",
            "validation": {"max_length": 64},
        }
    )
    definitions["fieldsets"].append(  # type: ignore[index]
        {
            "id": "local/extra",
            "label": "Local",
            "description": "",
            "lifecycle": "active",
            "fields": ["local/local__note"],
        }
    )
    definitions["asset_types"].append(  # type: ignore[index]
        {
            "id": "local/device",
            "manufacturer": "catalog/acme",
            "model": "Local device",
            "part_number": "",
            "gtin": None,
            "region": "",
            "configuration": "",
            "category": None,
            "description": "",
            "lifecycle": "active",
            "fieldsets": ["local/extra"],
            "specifications": {"local__note": "hello"},
            "historical_specifications": {},
        }
    )

    result = validate_library_document(json.dumps(snapshot))

    assert any(
        item["id"] == "local/device" for item in result.normalized_document["effective_definitions"]["asset_types"]
    )


def test_snapshot_rejects_active_same_namespace_additions():
    release = _release_document()
    snapshot = {
        "schema_version": 1,
        "kind": "itambox.type-library.snapshot",
        "upstream": deepcopy(release),
        "effective_definitions": deepcopy(release["definitions"]),
    }
    snapshot["effective_definitions"]["fields"].append(  # type: ignore[index]
        {
            "key": "acme__new",
            "namespace": "acme",
            "label": "New",
            "help_text": "",
            "targets": ["asset_type"],
            "activation": "composed",
            "field_type": "text",
            "required": False,
            "nullable": False,
            "lifecycle": "active",
            "validation": {"max_length": 32},
        }
    )

    with pytest.raises(LibraryValidationError) as caught:
        validate_library_document(json.dumps(snapshot))

    assert caught.value.code == "NAMESPACE_TAKEOVER"


def test_snapshot_rejects_immutable_upstream_field_changes():
    release = _release_document()
    snapshot = {
        "schema_version": 1,
        "kind": "itambox.type-library.snapshot",
        "upstream": deepcopy(release),
        "effective_definitions": deepcopy(release["definitions"]),
    }
    snapshot["effective_definitions"]["fields"][0]["nullable"] = True  # type: ignore[index]

    with pytest.raises(LibraryValidationError) as caught:
        validate_library_document(json.dumps(snapshot))

    assert caught.value.code == "IMMUTABLE_DEFINITION"


def test_publisher_namespace_length_is_bounded_before_graph_validation():
    document = _release_document()
    document["library"]["namespace"] = "a" * 63  # type: ignore[index]

    with pytest.raises(LibraryValidationError) as caught:
        validate_library_document(json.dumps(document))

    assert caught.value.code == "INVALID_IDENTITY"


def test_empty_gtin_is_rejected_instead_of_short_circuiting_pattern_validation():
    document = _release_document()
    document["definitions"]["asset_types"][0]["gtin"] = ""  # type: ignore[index]

    with pytest.raises(LibraryValidationError) as caught:
        validate_library_document(json.dumps(document))

    assert caught.value.code == "INVALID_VALIDATION"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# ---------------------------------------------------------------------------
# Focused matrices for the bounded, side-effect-free Type Library validator.
# ---------------------------------------------------------------------------

_DIGEST = "sha256:" + "a" * 64


def _at(document, *path):
    node = document
    for key in path:
        node = node[key]
    return node


def _set(document, path, value):
    _at(document, *path[:-1])[path[-1]] = value
    return document


def _drop(document, path):
    del _at(document, *path[:-1])[path[-1]]
    return document


def _reject(document, **kwargs) -> LibraryValidationError:
    with pytest.raises(LibraryValidationError) as caught:
        validate_library_document(json.dumps(document), **kwargs)
    return caught.value


def _codes(error: LibraryValidationError) -> set[str]:
    return {issue.code for issue in error.issues}


def _reject_with(document, code, **kwargs) -> LibraryValidationError:
    error = _reject(document, **kwargs)
    assert code in _codes(error), sorted(_codes(error))
    return error


def _spec(document, key, value):
    return _set(document, ("definitions", "asset_types", 0, "specifications", key), value)


def _field(index=1, **overrides):
    field = {
        "key": "acme__count",
        "namespace": "acme",
        "label": "Count",
        "help_text": "",
        "targets": ["asset_type"],
        "activation": "composed",
        "field_type": "integer",
        "required": False,
        "nullable": False,
        "lifecycle": "active",
        "validation": {},
    }
    field.update(overrides)
    return field


def _compose(document, field, values=None):
    definitions = _at(document, "definitions")
    definitions["fields"].append(field)
    definitions["fieldsets"][0]["fields"].append(f"{field['namespace']}/{field['key']}")
    if values is not None:
        _at(document, "definitions", "asset_types", 0)["specifications"].update(values)
    return field


def _snapshot_document():
    release = _release_document()
    return {
        "schema_version": 1,
        "kind": "itambox.type-library.snapshot",
        "upstream": deepcopy(release),
        "effective_definitions": deepcopy(release["definitions"]),
    }


# --- dependency anchors ----------------------------------------------------


def test_installed_dependency_anchors_accept_every_documented_spelling():
    document = _release_document()
    _set(document, ("requires",), [{"namespace": "core", "release": 2, "digest": _DIGEST}])
    forms = (
        {("core", 2): _DIGEST},
        {"core": {"release": 2, "digest": _DIGEST}},
        {"core": {"release": 2, "digest": _DIGEST, "document": {"kind": "probe"}}},
        {("core", 2): {"release": 2, "digest": _DIGEST}},
        {("core", 2): {"digest": _DIGEST}},
        {("core", 2): InstalledDependency("core", 2, _DIGEST)},
        {("core", 2): InstalledDependency("core", 2, _DIGEST, {"kind": "probe"})},
        [InstalledDependency("core", 2, _DIGEST)],
        (InstalledDependency("core", 2, _DIGEST),),
    )

    for form in forms:
        result = validate_library_document(json.dumps(document), installed_dependencies=form)

        assert result.normalized_document["requires"][0]["namespace"] == "core"


@pytest.mark.parametrize(
    "anchors",
    (
        5,
        {2: _DIGEST},
        {"core": 5},
        {"core": {"release": "2", "digest": _DIGEST}},
        {"core": {"release": 2, "digest": 7}},
        {("core", 2): 5},
        {("core", 2): {"release": "2", "digest": _DIGEST}},
    ),
)
def test_installed_dependency_anchors_require_typed_records(anchors):
    with pytest.raises(TypeError):
        validate_library_document(json.dumps(_release_document()), installed_dependencies=anchors)


@pytest.mark.parametrize(
    "anchors",
    (
        [InstalledDependency("Bad", 1, _DIGEST)],
        [InstalledDependency("core", 0, _DIGEST)],
        [InstalledDependency("core", 1, "sha256:deadbeef")],
    ),
)
def test_installed_dependency_anchors_are_identity_checked(anchors):
    _reject_with(_release_document(), "INVALID_IDENTITY", installed_dependencies=anchors)


def test_repeated_installed_dependency_anchors_are_rejected():
    anchors = [InstalledDependency("core", 1, _DIGEST), InstalledDependency("core", 1, _DIGEST)]

    _reject_with(_release_document(), "DUPLICATE_DEPENDENCY", installed_dependencies=anchors)


@pytest.mark.parametrize(
    "limits",
    (
        {"nope": 1},
        {"max_fields": -1},
        {"max_fields": True},
        {"max_fields": "8"},
        5,
    ),
)
def test_validation_limits_reject_unknown_or_ill_typed_bounds(limits):
    with pytest.raises((TypeError, ValueError)):
        validate_library_document(json.dumps(_release_document()), limits=limits)


def test_validation_limit_objects_and_aggregate_bounds_are_honoured():
    result = validate_library_document(json.dumps(_release_document()), limits=ValidationLimits())

    assert result.kind == "itambox.type-library.release"
    _reject_with(_release_document(), "RESOURCE_LIMIT", limits=ValidationLimits(max_fields=1))
    _reject_with(_release_document(), "RESOURCE_LIMIT", limits={"max_choice_sets": 0})
    _reject_with(_release_document(), "RESOURCE_LIMIT", limits={"max_total_choices": 1})


def test_dependency_reference_value_object_exposes_the_requested_identity():
    reference = DependencyReference("core", 3, _DIGEST)

    assert (reference.namespace, reference.release, reference.digest) == ("core", 3, _DIGEST)


# --- field surface ---------------------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "code"),
    (
        (lambda d: _set(d, ("definitions", "fields", 0, "label"), ""), "SCHEMA_TYPE"),
        (lambda d: _set(d, ("definitions", "fields", 0, "label"), 5), "SCHEMA_TYPE"),
        (lambda d: _set(d, ("definitions", "fields", 0, "label"), "x" * 201), "INVALID_RANGE"),
        (lambda d: _set(d, ("definitions", "fields", 0, "help_text"), 5), "SCHEMA_TYPE"),
        (lambda d: _set(d, ("definitions", "fields", 0, "help_text"), "x" * 4097), "INVALID_RANGE"),
        (lambda d: _set(d, ("definitions", "fields", 0, "targets"), []), "INVALID_RANGE"),
        (lambda d: _set(d, ("definitions", "fields", 0, "targets"), ["asset", "asset_type", "asset"]), "INVALID_RANGE"),
        (lambda d: _set(d, ("definitions", "fields", 0, "targets"), "asset_type"), "SCHEMA_TYPE"),
        (lambda d: _set(d, ("definitions", "fields", 0, "targets"), [1]), "SCHEMA_TYPE"),
        (lambda d: _set(d, ("definitions", "fields", 0, "targets"), ["nope"]), "INVALID_VALIDATION"),
        (lambda d: _set(d, ("definitions", "fields", 0, "targets"), ["asset", "asset"]), "DUPLICATE_IDENTITY"),
        (lambda d: _set(d, ("definitions", "fields", 0, "activation"), "sometimes"), "INVALID_VALIDATION"),
        (lambda d: _set(d, ("definitions", "fields", 0, "field_type"), "float"), "INVALID_VALIDATION"),
        (lambda d: _set(d, ("definitions", "fields", 0, "required"), "no"), "SCHEMA_TYPE"),
        (lambda d: _set(d, ("definitions", "fields", 0, "nullable"), 1), "SCHEMA_TYPE"),
        (lambda d: _set(d, ("definitions", "fields", 0, "lifecycle"), "retired"), "INVALID_VALIDATION"),
        (lambda d: _set(d, ("definitions", "fields", 0, "key"), "acme_state"), "INVALID_IDENTITY"),
        (lambda d: _set(d, ("definitions", "fields", 0, "key"), "acme__"), "INVALID_IDENTITY"),
        (lambda d: _set(d, ("definitions", "fields", 0, "key"), "Acme__state"), "INVALID_IDENTITY"),
        (lambda d: _set(d, ("definitions", "fields", 0, "namespace"), "other"), "NAMESPACE_TAKEOVER"),
        (lambda d: _set(d, ("definitions", "fields", 0, "namespace"), "catalog"), "NAMESPACE_TAKEOVER"),
        (lambda d: _set(d, ("definitions", "fields", 0, "namespace"), "a" * 63), "INVALID_IDENTITY"),
        (lambda d: _set(d, ("definitions", "fields", 0, "extra"), 1), "UNKNOWN_PROPERTY"),
        (lambda d: _drop(d, ("definitions", "fields", 0, "validation")), "MISSING_PROPERTY"),
        (lambda d: _drop(d, ("definitions", "fields", 0, "nullable")), "MISSING_PROPERTY"),
        (lambda d: _set(d, ("definitions", "fields", 0, "label"), "State\x00"), "INVALID_TYPE"),
    ),
)
def test_field_surface_rules_reject_unusable_shapes(mutate, code):
    document = _release_document()
    mutate(document)

    _reject_with(document, code)


def test_field_storage_keys_are_globally_unique_per_release():
    document = _release_document()
    _at(document, "definitions", "fields").append(deepcopy(_at(document, "definitions", "fields", 0)))

    _reject_with(document, "DUPLICATE_IDENTITY")


# --- field metadata --------------------------------------------------------


def _as_text(document, **validation):
    _spec(document, "acme__capacity", "1.2")
    field = _at(document, "definitions", "fields", 1)
    field["field_type"] = "text"
    field["validation"] = {"max_length": 64, **validation}
    field.pop("quantity_kind", None)
    field.pop("canonical_unit", None)
    return field


def _as_integer(document, **validation):
    _spec(document, "acme__capacity", 5)
    field = _at(document, "definitions", "fields", 1)
    field["field_type"] = "integer"
    field["validation"] = dict(validation)
    field.pop("quantity_kind", None)
    field.pop("canonical_unit", None)
    return field


def _as_boolean(document, **validation):
    _spec(document, "acme__capacity", True)
    field = _at(document, "definitions", "fields", 1)
    field["field_type"] = "boolean"
    field["validation"] = dict(validation)
    field.pop("quantity_kind", None)
    field.pop("canonical_unit", None)
    return field


def _as_date(document, **validation):
    field = _as_boolean(document, **validation)
    _spec(document, "acme__capacity", "2024-01-01")
    field["field_type"] = "date"
    return field


def _as_single_select(document, **validation):
    _spec(document, "acme__capacity", "on")
    field = _at(document, "definitions", "fields", 1)
    field["field_type"] = "single-select"
    field["validation"] = dict(validation)
    field["choice_set"] = "acme/state"
    field.pop("quantity_kind", None)
    field.pop("canonical_unit", None)
    return field


@pytest.mark.parametrize(
    ("prepare", "code"),
    (
        (lambda d: _as_text(d, max_length=0), "INVALID_RANGE"),
        (lambda d: _as_text(d, max_length=4097), "INVALID_RANGE"),
        (lambda d: _as_text(d, max_length="8"), "SCHEMA_TYPE"),
        (lambda d: _as_text(d, max_length=True), "SCHEMA_TYPE"),
        (lambda d: _as_text(d, regex="x" * 257), "INVALID_VALIDATION"),
        (lambda d: _as_text(d, regex="(?i)abc"), "INVALID_VALIDATION"),
        (lambda d: _as_text(d, regex="(a)\\1"), "INVALID_VALIDATION"),
        (lambda d: _as_text(d, regex="(?P<n>a)(?P=n)"), "INVALID_VALIDATION"),
        (lambda d: _as_text(d, regex="["), "INVALID_VALIDATION"),
        (lambda d: _as_text(d, regex="a+b+"), "INVALID_VALIDATION"),
        (lambda d: _as_text(d, rule="anything"), "INVALID_VALIDATION"),
        (lambda d: _as_text(d, scale=2), "INVALID_VALIDATION"),
        (lambda d: _as_text(d, max_values=1), "INVALID_VALIDATION"),
        (lambda d: _as_integer(d, scale=1), "INVALID_VALIDATION"),
        (lambda d: _as_integer(d, max_length=4), "INVALID_VALIDATION"),
        (lambda d: _as_integer(d, minimum="4", maximum="2"), "INVALID_RANGE"),
        (lambda d: _as_integer(d, minimum="abc"), "INVALID_VALIDATION"),
        (lambda d: _as_integer(d, minimum=5), "SCHEMA_TYPE"),
        (lambda d: _as_integer(d, minimum="-0"), "INVALID_VALIDATION"),
        (lambda d: _as_integer(d, minimum="1e5"), "INVALID_VALIDATION"),
        (lambda d: _as_integer(d, minimum="1.0000000"), "INVALID_VALIDATION"),
        (lambda d: _as_boolean(d, max_values=1), "INVALID_VALIDATION"),
        (lambda d: _as_date(d, scale=3), "INVALID_VALIDATION"),
        (lambda d: _as_single_select(d, max_values=2), "INVALID_VALIDATION"),
        (lambda d: _as_single_select(d), "INVALID_VALIDATION"),
        (lambda d: _as_single_select(d, max_values="1"), "INVALID_VALIDATION"),
        (lambda d: _as_single_select(d, max_values=1, scale=3), "INVALID_VALIDATION"),
        (lambda d: _as_text(d), "MISSING_PROPERTY"),
    ),
)
def test_field_metadata_rules_reject_unusable_shapes(prepare, code):
    document = _release_document()
    prepare(document)
    if code == "MISSING_PROPERTY":
        _set(document, ("definitions", "fields", 1, "validation"), {})

    _reject_with(document, code)


@pytest.mark.parametrize(
    "prepare",
    (
        lambda d: _as_text(d, rule="rfc1123_hostname"),
        lambda d: _as_text(d, regex=r"[0-9.]+"),
        lambda d: _as_integer(d, minimum="1", maximum="10"),
        lambda d: _as_integer(d, minimum="5.0"),
        lambda d: _as_boolean(d),
        lambda d: _as_date(d),
        lambda d: _as_single_select(d, max_values=1),
    ),
)
def test_documented_field_metadata_shapes_validate(prepare):
    document = _release_document()
    prepare(document)

    assert validate_library_document(json.dumps(document)).normalized_document


def test_decimal_metadata_requires_scale_and_bounded_values():
    document = _release_document()
    _set(document, ("definitions", "fields", 1, "validation"), {})

    _reject_with(document, "MISSING_PROPERTY")

    document = _release_document()
    _set(document, ("definitions", "fields", 1, "validation"), {"minimum": "0.000"})
    _reject_with(document, "MISSING_PROPERTY")

    document = _release_document()
    _set(document, ("definitions", "fields", 1, "validation"), {"scale": 3, "min_length": 1})
    _reject_with(document, "UNKNOWN_PROPERTY")


# --- quantity metadata -----------------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "code"),
    (
        (lambda d: _drop(d, ("definitions", "fields", 1, "canonical_unit")), "INVALID_VALIDATION"),
        (lambda d: _drop(d, ("definitions", "fields", 1, "quantity_kind")), "INVALID_VALIDATION"),
        (lambda d: _set(d, ("definitions", "fields", 1, "quantity_kind"), 5), "SCHEMA_TYPE"),
        (lambda d: _set(d, ("definitions", "fields", 1, "canonical_unit"), 5), "SCHEMA_TYPE"),
        (lambda d: _set(d, ("definitions", "fields", 1, "quantity_kind"), "x" * 33), "INVALID_RANGE"),
        (lambda d: _set(d, ("definitions", "fields", 1, "canonical_unit"), "x" * 17), "INVALID_RANGE"),
        (lambda d: _set(d, ("definitions", "fields", 1, "canonical_unit"), "m"), "INVALID_VALIDATION"),
        (lambda d: _set(d, ("definitions", "fields", 1, "quantity_kind"), "nope"), "INVALID_VALIDATION"),
        (
            lambda d: (
                _set(d, ("definitions", "fields", 1, "quantity_kind"), "mass"),
                _set(d, ("definitions", "fields", 1, "canonical_unit"), "m"),
            ),
            "INVALID_VALIDATION",
        ),
        (
            lambda d: (
                _set(d, ("definitions", "fields", 1, "field_type"), "text"),
                _set(d, ("definitions", "fields", 1, "validation"), {"max_length": 8}),
            ),
            "INVALID_VALIDATION",
        ),
    ),
)
def test_quantity_metadata_rules_reject_unusable_shapes(mutate, code):
    document = _release_document()
    mutate(document)

    _reject_with(document, code)


def test_compatible_quantity_metadata_validates():
    document = _release_document()
    _set(document, ("definitions", "fields", 1, "quantity_kind"), "digital_information")
    _set(document, ("definitions", "fields", 1, "canonical_unit"), "GiB")

    assert validate_library_document(json.dumps(document)).normalized_document


# --- field choice wiring ---------------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "code"),
    (
        (lambda d: _drop(d, ("definitions", "fields", 0, "choice_set")), "MISSING_PROPERTY"),
        (lambda d: _set(d, ("definitions", "fields", 0, "choice_set"), "nope__x"), "INVALID_IDENTITY"),
        (lambda d: _set(d, ("definitions", "fields", 0, "replaced_by"), "nope"), "INVALID_IDENTITY"),
        (lambda d: _set(d, ("definitions", "fields", 1, "choice_set"), "acme/state"), "INVALID_VALIDATION"),
        (lambda d: _set(d, ("definitions", "fields", 0, "choice_set"), "acme/missing"), "INVALID_REFERENCE"),
    ),
)
def test_field_choice_wiring_rules(mutate, code):
    document = _release_document()
    mutate(document)

    _reject_with(document, code)


# ---------------------------------------------------------------------------
# Typed specification values, cross-field rules, references, composition.
# ---------------------------------------------------------------------------


def _core_document(specifications=None):
    """Core-namespace release: rule-bearing keys stay unprefixed by contract."""
    fields = [
        _field(
            namespace="itambox",
            key="operating_temperature_min",
            label="Temperature Minimum",
            field_type="decimal",
            validation={"scale": 1},
        ),
        _field(
            namespace="itambox",
            key="operating_temperature_max",
            label="Temperature Maximum",
            field_type="decimal",
            validation={"scale": 1, "rule": "temperature_max_gte_min"},
        ),
        _field(
            namespace="itambox",
            key="input_voltage_min",
            label="Voltage Minimum",
            field_type="decimal",
            validation={"scale": 1},
        ),
        _field(
            namespace="itambox",
            key="input_voltage_max",
            label="Voltage Maximum",
            field_type="decimal",
            validation={"scale": 1, "rule": "voltage_max_gte_min"},
        ),
        _field(
            namespace="itambox",
            key="battery_runtime",
            label="Battery Runtime",
            field_type="decimal",
            validation={"scale": 1, "rule": "runtime_requires_load"},
        ),
        _field(
            namespace="itambox",
            key="battery_runtime_load",
            label="Battery Runtime Load",
            field_type="decimal",
            validation={"scale": 1},
        ),
    ]
    return {
        "schema_version": 1,
        "kind": "itambox.type-library.release",
        "library": {"namespace": "itambox", "release": 3, "label": "Core"},
        "requires": [],
        "definitions": {
            "choice_sets": [],
            "fields": fields,
            "fieldsets": [
                {
                    "id": "itambox/specs",
                    "label": "Specs",
                    "description": "Specs",
                    "lifecycle": "active",
                    "fields": [f"itambox/{field['key']}" for field in fields],
                }
            ],
            "categories": [],
            "manufacturers": [],
            "asset_types": [
                {
                    "id": "itambox/device",
                    "manufacturer": "catalog/acme",
                    "model": "Device",
                    "part_number": "A",
                    "gtin": None,
                    "region": "",
                    "configuration": "",
                    "category": None,
                    "description": "Device",
                    "lifecycle": "active",
                    "fieldsets": ["itambox/specs"],
                    "specifications": dict(specifications or {}),
                    "historical_specifications": {},
                }
            ],
        },
    }


def _as_type(document, field_type, validation, value, index=1):
    definition = document["definitions"]["fields"][index]
    definition.update(field_type=field_type, validation=validation, required=False, nullable=False)
    definition.pop("quantity_kind", None)
    definition.pop("canonical_unit", None)
    definition.pop("choice_set", None)
    _spec(document, definition["key"], value)
    return document


def _assert_outcome(document, code, **kwargs):
    if code is None:
        assert validate_library_document(json.dumps(document), **kwargs).normalized_document
        return
    _reject_with(document, code, **kwargs)


@pytest.mark.parametrize(
    ("value", "code"),
    (
        ("1.2", None),
        ("1.234", None),
        ("0", None),
        ("99.999", None),
        ("1.2345", "INVALID_RANGE"),
        ("-1", "INVALID_RANGE"),
        ("100", "INVALID_RANGE"),
        ("-0", "INVALID_TYPE"),
        ("-0.0", "INVALID_TYPE"),
        ("1e3", "INVALID_TYPE"),
        ("", "INVALID_TYPE"),
        (1.2, "UNSUPPORTED_STRUCTURE"),
    ),
)
def test_decimal_values_follow_scale_and_bound_contracts(value, code):
    _assert_outcome(
        _as_type(_release_document(), "decimal", {"scale": 3, "minimum": "0.000", "maximum": "99.999"}, value), code
    )


@pytest.mark.parametrize(
    ("value", "code"),
    (
        (1, None),
        (10, None),
        (0, "INVALID_RANGE"),
        (11, "INVALID_RANGE"),
        ("5", "SCHEMA_TYPE"),
        (True, "SCHEMA_TYPE"),
        (5.0, "UNSUPPORTED_STRUCTURE"),
        (9007199254740992, "INVALID_RANGE"),
    ),
)
def test_integer_values_follow_bound_and_type_contracts(value, code):
    _assert_outcome(_as_type(_release_document(), "integer", {"minimum": "1", "maximum": "10"}, value), code)


@pytest.mark.parametrize(
    ("value", "code"),
    ((True, None), (False, None), ("true", "SCHEMA_TYPE"), (1, "SCHEMA_TYPE"), (None, "INVALID_TYPE")),
)
def test_boolean_values_reject_python_truthiness(value, code):
    _assert_outcome(_as_type(_release_document(), "boolean", {}, value), code)


@pytest.mark.parametrize(
    ("value", "code"),
    (
        ("2024-02-29", None),
        ("2023-02-29", "INVALID_VALUE"),
        ("2024-1-1", "INVALID_VALUE"),
        ("2024-01-01T00:00:00", "INVALID_VALUE"),
        (20240101, "SCHEMA_TYPE"),
    ),
)
def test_date_values_require_canonical_iso_calendar_days(value, code):
    _assert_outcome(_as_type(_release_document(), "date", {}, value), code)


@pytest.mark.parametrize(
    ("value", "code"),
    (("abcdefgh", None), ("", None), ("   ", None), ("abcdefghi", "INVALID_RANGE")),
)
def test_text_values_honour_optional_empty_and_max_length(value, code):
    _assert_outcome(_as_type(_release_document(), "text", {"max_length": 8}, value), code)


@pytest.mark.parametrize(
    ("value", "code"),
    (
        (["on", "off"], None),
        (["off", "on"], None),
        ([], None),
        (["on", "on"], "DUPLICATE_VALUE"),
        (["on", "off", "x"], "INVALID_RANGE"),
        (["x"], "INVALID_CHOICE"),
        ("on", "SCHEMA_TYPE"),
        (None, "INVALID_TYPE"),
    ),
)
def test_multi_select_values_validate_shape_duplicates_and_choice_availability(value, code):
    _assert_outcome(_spec(_release_document(), "acme__state", value), code)


@pytest.mark.parametrize(
    ("required", "nullable", "value", "code"),
    (
        (False, True, None, None),
        (True, True, None, "REQUIRED_FIELD"),
        (True, False, None, "REQUIRED_FIELD"),
        (False, False, None, "INVALID_TYPE"),
    ),
)
def test_null_is_only_a_value_when_the_field_allows_it(required, nullable, value, code):
    document = _release_document()
    _as_type(document, "text", {"max_length": 8}, value)
    document["definitions"]["fields"][1].update(required=required, nullable=nullable)

    _assert_outcome(document, code)


@pytest.mark.parametrize(
    ("field_type", "validation", "value"),
    (
        ("boolean", {}, False),
        ("integer", {"minimum": "0", "maximum": "10"}, 0),
        ("multi-select", {"max_values": 2}, []),
        ("text", {"max_length": 8}, ""),
    ),
)
def test_falsy_and_empty_values_still_satisfy_required_fields_except_empties(field_type, validation, value):
    document = _release_document()
    if field_type == "multi-select":
        _as_type(document, field_type, validation, value, index=0)
        document["definitions"]["fields"][0].update(choice_set="acme/state")
    else:
        _as_type(document, field_type, validation, value)
    document["definitions"]["fields"][0 if field_type == "multi-select" else 1].update(required=True)

    expected = "REQUIRED_FIELD" if field_type in {"multi-select", "text"} else None
    _assert_outcome(document, expected)


def test_required_field_missing_from_specifications_is_reported():
    document = _release_document()
    _drop(document, ("definitions", "asset_types", 0, "specifications", "acme__capacity"))
    document["definitions"]["fields"][1].update(required=True)

    _reject_with(document, "REQUIRED_FIELD")


@pytest.mark.parametrize(
    ("specifications", "code"),
    (
        ({"operating_temperature_min": "-10.0", "operating_temperature_max": "40.0"}, None),
        ({"operating_temperature_min": "10.0", "operating_temperature_max": "5.0"}, "INVALID_RANGE"),
        ({"operating_temperature_min": "10.0"}, None),
        ({"operating_temperature_max": "5.0"}, None),
        ({"input_voltage_min": "240.0", "input_voltage_max": "120.0"}, "INVALID_RANGE"),
        ({"input_voltage_min": "120.0", "input_voltage_max": "240.0"}, None),
        ({"battery_runtime": "4.0"}, "REQUIRED_FIELD"),
        ({"battery_runtime": "4.0", "battery_runtime_load": "0.5"}, None),
        ({"battery_runtime_load": "0.5"}, None),
    ),
)
def test_cross_field_rules_only_fire_on_present_value_pairs(specifications, code):
    _assert_outcome(_core_document(specifications), code)


def test_cross_field_rules_compare_normalized_decimals():
    document = _core_document({"operating_temperature_min": "10", "operating_temperature_max": "10.0"})

    result = validate_library_document(json.dumps(document))

    assert result.normalized_document["definitions"]["asset_types"][0]["specifications"] == {
        "operating_temperature_min": "10.0",
        "operating_temperature_max": "10.0",
    }


# --- choice availability and replacement -----------------------------------


def test_active_select_field_requires_an_active_choice_set():
    _reject_with(
        _set(_release_document(), ("definitions", "choice_sets", 0, "lifecycle"), "deprecated"), "DEPENDENCY_RETIREMENT"
    )


def test_active_select_field_requires_at_least_one_active_choice():
    document = _release_document()
    for index in range(2):
        _set(document, ("definitions", "choice_sets", 0, "choices", index, "lifecycle"), "deprecated")

    _reject_with(document, "DEPENDENCY_RETIREMENT")


def test_deprecated_select_field_may_keep_a_deprecated_choice_set():
    document = _release_document()
    _set(document, ("definitions", "choice_sets", 0, "lifecycle"), "deprecated")
    _set(document, ("definitions", "fields", 0, "lifecycle"), "deprecated")
    _drop(document, ("definitions", "asset_types", 0, "specifications", "acme__state"))

    _assert_outcome(document, None)


def test_choice_replacement_target_must_resolve_inside_the_same_set():
    document = _release_document()
    _set(document, ("definitions", "choice_sets", 0, "choices", 0, "replaced_by"), "nope")

    _reject_with(document, "INVALID_REFERENCE")


def test_choice_replacement_between_active_choices_is_valid():
    document = _release_document()
    _set(document, ("definitions", "choice_sets", 0, "choices", 0, "replaced_by"), "off")

    _assert_outcome(document, None)


def test_duplicate_choice_keys_are_rejected():
    _reject_with(
        _set(_release_document(), ("definitions", "choice_sets", 0, "choices", 1, "key"), "on"), "DUPLICATE_IDENTITY"
    )


def test_choice_set_replacement_must_resolve():
    _reject_with(
        _set(_release_document(), ("definitions", "choice_sets", 0, "replaced_by"), "acme/missing"), "INVALID_REFERENCE"
    )


def test_choice_set_replacing_itself_is_a_cycle():
    _reject_with(
        _set(_release_document(), ("definitions", "choice_sets", 0, "replaced_by"), "acme/state"), "REFERENCE_CYCLE"
    )


def test_declared_dependency_choice_set_without_a_loaded_graph_fails_closed():
    document = _release_document()
    _set(document, ("requires",), [{"namespace": "core", "release": 2, "digest": _DIGEST}])
    _set(document, ("definitions", "fields", 0, "choice_set"), "core/states")

    _reject_with(document, "DEPENDENCY_GRAPH_UNAVAILABLE", installed_dependencies={("core", 2): _DIGEST})


def test_deprecated_choice_set_cannot_receive_current_multi_values():
    document = _release_document()
    _set(document, ("definitions", "choice_sets", 0, "choices", 1, "lifecycle"), "deprecated")

    _reject_with(document, "INVALID_CHOICE")


# --- composition and applicability -----------------------------------------


def test_unknown_specification_key_is_rejected():
    _reject_with(_spec(_release_document(), "acme__unknown", 1), "UNKNOWN_FIELD_KEY")


def test_declared_field_outside_the_composition_cannot_receive_values():
    document = _release_document()
    _set(document, ("definitions", "fieldsets", 0, "fields"), ["acme/acme__state"])

    _reject_with(document, "INVALID_APPLICABILITY")


def test_field_without_the_asset_type_target_cannot_receive_values():
    document = _release_document()
    _set(document, ("definitions", "fields", 1, "targets"), ["asset"])

    _reject_with(document, "INVALID_APPLICABILITY")


def test_deprecated_field_is_not_composed_for_current_values():
    document = _set(_release_document(), ("definitions", "fields", 1, "lifecycle"), "deprecated")

    _reject_with(document, "INVALID_APPLICABILITY")


def test_global_field_joins_the_composition_without_a_fieldset():
    document = _release_document()
    document["definitions"]["fields"].append(
        _field(key="acme__serial", label="Serial", activation="global", required=True)
    )

    _reject_with(document, "REQUIRED_FIELD")


def test_global_field_inside_a_fieldset_is_rejected():
    document = _release_document()
    document["definitions"]["fields"].append(_field(key="acme__serial", label="Serial", activation="global"))
    _at(document, "definitions", "fieldsets", 0, "fields").append("acme/acme__serial")

    _reject_with(document, "INVALID_APPLICABILITY")


def test_fieldsets_are_unique_per_asset_type():
    _reject_with(
        _set(_release_document(), ("definitions", "asset_types", 0, "fieldsets"), ["acme/specs", "acme/specs"]),
        "DUPLICATE_IDENTITY",
    )


def test_a_field_occurs_once_per_fieldset():
    document = _release_document()
    _set(document, ("definitions", "fieldsets", 0, "fields"), ["acme/acme__capacity", "acme/acme__capacity"])

    _reject_with(document, "DUPLICATE_IDENTITY")


def test_asset_type_fieldset_reference_must_resolve():
    _reject_with(
        _set(_release_document(), ("definitions", "asset_types", 0, "fieldsets"), ["acme/missing"]), "INVALID_REFERENCE"
    )


def test_asset_type_self_replacement_is_a_cycle():
    _reject_with(
        _set(_release_document(), ("definitions", "asset_types", 0, "replaced_by"), "acme/device-a"), "REFERENCE_CYCLE"
    )


def test_asset_type_replacement_must_resolve():
    _reject_with(
        _set(_release_document(), ("definitions", "asset_types", 0, "replaced_by"), "acme/device-b"),
        "INVALID_REFERENCE",
    )


def test_category_must_apply_to_assets_for_asset_types():
    _reject_with(
        _set(_release_document(), ("definitions", "categories", 0, "applies_to"), ["accessory"]),
        "INVALID_APPLICABILITY",
    )


def test_category_default_fieldset_reference_must_resolve():
    _reject_with(
        _set(_release_document(), ("definitions", "categories", 0, "default_fieldsets"), ["acme/missing"]),
        "INVALID_REFERENCE",
    )


def test_category_default_fieldsets_must_be_unique():
    document = _release_document()
    _set(document, ("definitions", "categories", 0, "default_fieldsets"), ["acme/specs", "acme/specs"])

    _reject_with(document, "DUPLICATE_IDENTITY")


# --- historical specification records --------------------------------------


@pytest.mark.parametrize(
    ("record", "code"),
    (
        ({"value": "1.2", "reason": "removed_composition"}, None),
        ({"value": "1.2", "reason": "deprecated_definition"}, None),
        ({"value": "1.2", "reason": "deprecated_choice"}, None),
        ({"value": "1.2", "reason": "because"}, "INVALID_VALIDATION"),
        ({"value": "1.2"}, "MISSING_PROPERTY"),
        ({"value": "1.2", "reason": "removed_composition", "extra": 1}, "UNKNOWN_PROPERTY"),
        ({"value": True, "reason": "removed_composition"}, "INVALID_TYPE"),
        ({"value": 5, "reason": "removed_composition"}, "INVALID_TYPE"),
    ),
)
def test_historical_records_require_a_known_reason_and_a_typed_value(record, code):
    _assert_outcome(
        _set(
            _release_document(),
            ("definitions", "asset_types", 0, "historical_specifications", "acme__capacity"),
            record,
        ),
        code,
    )


def test_historical_field_key_must_resolve():
    document = _release_document()
    _set(
        document,
        ("definitions", "asset_types", 0, "historical_specifications", "acme__unknown"),
        {"value": "1.2", "reason": "removed_composition"},
    )

    _reject_with(document, "INVALID_REFERENCE")


def test_historical_values_are_normalized_without_touching_current_values():
    document = _release_document()
    _set(
        document,
        ("definitions", "asset_types", 0, "historical_specifications", "acme__capacity"),
        {"value": "1.2", "reason": "removed_composition"},
    )

    result = validate_library_document(json.dumps(document))

    specifications = result.normalized_document["definitions"]["asset_types"][0]
    assert specifications["historical_specifications"]["acme__capacity"]["value"] == "1.200"
    assert specifications["specifications"]["acme__capacity"] == "1.200"


# --- resource envelope ------------------------------------------------------


@pytest.mark.parametrize(
    ("limits", "code"),
    (
        ({"max_fields": 1}, "RESOURCE_LIMIT"),
        ({"max_fieldsets": 0}, "RESOURCE_LIMIT"),
        ({"max_choice_sets": 0}, "RESOURCE_LIMIT"),
        ({"max_asset_types": 0}, "RESOURCE_LIMIT"),
        ({"max_choices_per_set": 1}, "RESOURCE_LIMIT"),
        ({"max_total_choices": 1}, "RESOURCE_LIMIT"),
        ({"max_fields_per_section": 1}, "RESOURCE_LIMIT"),
        ({"max_sections_per_type": 0}, "RESOURCE_LIMIT"),
        ({"max_effective_fields_per_type": 1}, "RESOURCE_LIMIT"),
        ({"max_specifications_per_type": 1}, "RESOURCE_LIMIT"),
        ({"max_dependencies": 0}, "RESOURCE_LIMIT"),
    ),
)
def test_resource_limits_are_counted_against_the_document(limits, code):
    document = _release_document()
    if "max_dependencies" in limits:
        _set(document, ("requires",), [{"namespace": "core", "release": 2, "digest": _DIGEST}])

    _reject_with(document, code, limits=limits)


def test_historical_entries_are_counted_against_their_own_limit():
    document = _release_document()
    _set(
        document,
        ("definitions", "asset_types", 0, "historical_specifications", "acme__capacity"),
        {"value": "1.2", "reason": "removed_composition"},
    )

    _reject_with(document, "RESOURCE_LIMIT", limits={"max_historical_specifications_per_type": 0})


# --- asset type surface -----------------------------------------------------


@pytest.mark.parametrize(
    ("path", "value", "code"),
    (
        (("gtin",), "123", "INVALID_VALIDATION"),
        (("gtin",), "4006381333931", None),
        (("gtin",), 4006381333931, "SCHEMA_TYPE"),
        (("manufacturer",), "acme/manufacturers", "NAMESPACE_TAKEOVER"),
        (("category",), "acme/devices", "NAMESPACE_TAKEOVER"),
        (("id",), "other/device", "NAMESPACE_TAKEOVER"),
        (("lifecycle",), "retired", "INVALID_VALIDATION"),
        (("region",), 5, "SCHEMA_TYPE"),
        (("model",), "x" * 256, "INVALID_RANGE"),
        (("part_number",), "x" * 101, "INVALID_RANGE"),
        (("region",), "x" * 65, "INVALID_RANGE"),
        (("configuration",), "x" * 256, "INVALID_RANGE"),
        (("description",), "x" * 4097, "INVALID_RANGE"),
    ),
)
def test_asset_type_surface_rules(path, value, code):
    _assert_outcome(_set(_release_document(), ("definitions", "asset_types", 0, *path), value), code)


# ---------------------------------------------------------------------------
# Release header, declared requirements, snapshots, normalization.
# ---------------------------------------------------------------------------

_CORE_DIGEST = "sha256:" + "a" * 64


def _put(document, path, value):
    node = document
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return document


def _snapshot_with(prepare):
    snapshot = _snapshot_document()
    prepare(snapshot)
    return snapshot


@pytest.mark.parametrize(
    ("path", "value", "code"),
    (
        (("schema_version",), 2, "UNSUPPORTED_VERSION"),
        (("schema_version",), "1", "UNSUPPORTED_VERSION"),
        (("kind",), "itambox.type-library.unknown", "UNSUPPORTED_KIND"),
        (("kind",), None, "UNSUPPORTED_KIND"),
        (("kind",), "itambox.type-library.snapshots", "UNSUPPORTED_KIND"),
        (("library", "namespace"), "catalog", "NAMESPACE_TAKEOVER"),
        (("library", "release"), 0, "INVALID_RANGE"),
        (("library", "release"), "3", "SCHEMA_TYPE"),
        (("library", "label"), "x" * 201, "INVALID_RANGE"),
    ),
)
def test_release_header_rejects_out_of_contract_values(path, value, code):
    _assert_outcome(_put(_release_document(), path, value), code)


def test_a_document_without_a_library_block_is_rejected():
    _assert_outcome(_drop(_release_document(), ("library",)), "MISSING_PROPERTY")


def test_a_document_without_definitions_is_rejected():
    _assert_outcome(_drop(_release_document(), ("definitions",)), "MISSING_PROPERTY")


def test_a_document_without_declared_requirements_is_rejected():
    _assert_outcome(_drop(_release_document(), ("requires",)), "MISSING_PROPERTY")


def test_unknown_root_properties_are_rejected():
    _assert_outcome(_put(_release_document(), ("unexpected",), 1), "UNKNOWN_PROPERTY")


def test_unknown_library_properties_are_rejected():
    _assert_outcome(_put(_release_document(), ("library", "unexpected"), 1), "UNKNOWN_PROPERTY")


def test_unknown_definition_sections_are_rejected():
    _assert_outcome(_put(_release_document(), ("definitions", "unexpected"), []), "UNKNOWN_PROPERTY")


# --- declared requirements -------------------------------------------------


def _requirement(**overrides):
    requirement = {"namespace": "core", "release": 2, "digest": _CORE_DIGEST}
    requirement.update(overrides)
    return requirement


@pytest.mark.parametrize(
    ("requirement", "installed", "code"),
    (
        (_requirement(namespace="acme"), [], "REFERENCE_CYCLE"),
        (_requirement(), [], "DEPENDENCY_MISSING"),
        (_requirement(), [InstalledDependency("core", 2, "sha256:" + "b" * 64)], "DEPENDENCY_DIGEST_MISMATCH"),
        (_requirement(digest="sha256:zz"), [InstalledDependency("core", 2, "sha256:zz")], "INVALID_IDENTITY"),
        (_requirement(digest="not-a-digest"), [InstalledDependency("core", 2, "not-a-digest")], "INVALID_IDENTITY"),
        (_requirement(namespace="Bad"), [], "INVALID_IDENTITY"),
        (_requirement(release=0), [], "INVALID_RANGE"),
        (_requirement(release=0), [InstalledDependency("core", 0, _CORE_DIGEST)], "INVALID_IDENTITY"),
        (_requirement(release="2"), [], "SCHEMA_TYPE"),
        (_requirement(unexpected="x"), [InstalledDependency("core", 2, _CORE_DIGEST)], "UNKNOWN_PROPERTY"),
    ),
)
def test_declared_requirements_are_validated(requirement, installed, code):
    document = _put(_release_document(), ("requires",), [requirement])
    _assert_outcome(document, code, installed_dependencies=installed)


def test_repeated_declared_requirements_are_rejected():
    document = _put(_release_document(), ("requires",), [_requirement(), _requirement()])
    _assert_outcome(
        document,
        "DUPLICATE_DEPENDENCY",
        installed_dependencies=[InstalledDependency("core", 2, _CORE_DIGEST)],
    )


def test_installed_requirements_are_normalized_into_the_document():
    document = _put(_release_document(), ("requires",), [_requirement()])
    result = validate_library_document(
        json.dumps(document),
        installed_dependencies=[InstalledDependency("core", 2, _CORE_DIGEST)],
    )
    assert result.normalized_document["requires"] == [{"namespace": "core", "release": 2, "digest": _CORE_DIGEST}]


def test_installed_but_unreferenced_requirements_are_kept():
    document = _put(
        _release_document(),
        ("requires",),
        [_requirement(), _requirement(namespace="other", release=1, digest="sha256:" + "c" * 64)],
    )
    result = validate_library_document(
        json.dumps(document),
        installed_dependencies=[
            InstalledDependency("core", 2, _CORE_DIGEST),
            InstalledDependency("other", 1, "sha256:" + "c" * 64),
        ],
    )
    namespaces = {entry["namespace"] for entry in result.normalized_document["requires"]}
    assert namespaces == {"core", "other"}


# --- snapshots -------------------------------------------------------------


def _snapshot_choice_sets(snapshot):
    return snapshot["effective_definitions"]["choice_sets"]


def test_a_snapshot_of_an_unchanged_release_is_valid():
    result = validate_library_document(json.dumps(_snapshot_document()))
    normalized = result.normalized_document
    assert normalized["kind"] == "itambox.type-library.snapshot"
    assert rfc8785.dumps(normalized) == result.canonical_bytes
    assert normalized["upstream"]["definitions"]["fields"] == normalized["effective_definitions"]["fields"]


def test_snapshot_upstream_is_normalized_like_a_release():
    snapshot = _snapshot_with(lambda document: _spec(document["upstream"], "acme__capacity", "1.2"))
    result = validate_library_document(json.dumps(snapshot))
    specifications = result.normalized_document["upstream"]["definitions"]["asset_types"][0]["specifications"]
    assert specifications["acme__capacity"] == "1.200"


def test_snapshot_effective_definitions_are_normalized_too():
    snapshot = _snapshot_with(
        lambda document: _put(
            document["effective_definitions"],
            ("choice_sets", 0, "choices", 1, "label"),
            "Off",
        )
    )
    result = validate_library_document(json.dumps(snapshot))
    definitions = result.normalized_document["effective_definitions"]
    assert definitions["choice_sets"][0]["choices"][1]["label"] == "Off"


def test_snapshot_may_retire_an_upstream_field():
    def prepare(document):
        definitions = document["effective_definitions"]
        field = definitions["fields"][1]
        field["lifecycle"] = "deprecated"
        definitions["asset_types"][0]["specifications"].pop(field["key"], None)

    result = validate_library_document(json.dumps(_snapshot_with(prepare)))
    fields = {field["key"]: field for field in result.normalized_document["effective_definitions"]["fields"]}
    assert fields["acme__capacity"]["lifecycle"] == "deprecated"


def test_snapshot_must_keep_every_upstream_choice():
    def prepare(document):
        choice_set = _snapshot_choice_sets(document)[0]
        choice_set["choices"].pop()
        document["effective_definitions"]["asset_types"][0]["specifications"]["acme__state"] = [
            choice["key"] for choice in choice_set["choices"]
        ]

    _assert_outcome(_snapshot_with(prepare), "HISTORICAL_CLOSURE")


def test_snapshot_must_keep_every_upstream_field():
    def prepare(document):
        definitions = document["effective_definitions"]
        removed = definitions["fields"].pop(0)
        identity = f"{removed['namespace']}/{removed['key']}"
        definitions["fieldsets"][0]["fields"].remove(identity)
        definitions["asset_types"][0]["specifications"].pop(removed["key"], None)
        definitions["asset_types"][0]["historical_specifications"].pop(removed["key"], None)

    _assert_outcome(_snapshot_with(prepare), "HISTORICAL_CLOSURE")


def test_snapshot_cannot_activate_a_new_field_in_the_upstream_namespace():
    def prepare(document):
        document["effective_definitions"]["fields"].append(
            _field(namespace="acme", key="acme__fresh", label="Fresh", field_type="integer")
        )

    _assert_outcome(_snapshot_with(prepare), "NAMESPACE_TAKEOVER")


def test_snapshot_may_add_a_deprecated_field_in_the_upstream_namespace():
    def prepare(document):
        field = _field(namespace="acme", key="acme__legacy", label="Legacy", field_type="integer")
        field["lifecycle"] = "deprecated"
        document["effective_definitions"]["fields"].append(field)

    result = validate_library_document(json.dumps(_snapshot_with(prepare)))
    keys = {field["key"] for field in result.normalized_document["effective_definitions"]["fields"]}
    assert "acme__legacy" in keys


def test_snapshot_may_add_a_foreign_namespace_field():
    def prepare(document):
        document["effective_definitions"]["fields"].append(
            _field(namespace="other", key="other__probe", label="Probe", field_type="integer")
        )

    result = validate_library_document(json.dumps(_snapshot_with(prepare)))
    keys = {field["key"] for field in result.normalized_document["effective_definitions"]["fields"]}
    assert "other__probe" in keys


def test_snapshot_cannot_activate_a_new_choice():
    def prepare(document):
        _snapshot_choice_sets(document)[0]["choices"].append({"key": "fresh", "label": "Fresh", "lifecycle": "active"})

    _assert_outcome(_snapshot_with(prepare), "NAMESPACE_TAKEOVER")


def test_snapshot_cannot_activate_a_new_category():
    def prepare(document):
        document["effective_definitions"]["categories"].append(
            {
                "id": "catalog/fresh",
                "label": "Fresh",
                "description": "",
                "lifecycle": "active",
                "applies_to": ["asset"],
                "default_fieldsets": [],
            }
        )

    _assert_outcome(_snapshot_with(prepare), "NAMESPACE_TAKEOVER")


def test_snapshot_cannot_change_an_immutable_field_property():
    def prepare(document):
        document["effective_definitions"]["fields"][1]["nullable"] = True

    _assert_outcome(_snapshot_with(prepare), "IMMUTABLE_DEFINITION")


def test_snapshot_cannot_change_an_immutable_asset_type_property():
    def prepare(document):
        document["effective_definitions"]["asset_types"][0]["part_number"] = "B"

    _assert_outcome(_snapshot_with(prepare), "IMMUTABLE_DEFINITION")


def test_snapshot_may_change_a_mutable_field_label():
    def prepare(document):
        document["effective_definitions"]["fields"][1]["label"] = "Renamed"

    result = validate_library_document(json.dumps(_snapshot_with(prepare)))
    fields = {field["key"]: field for field in result.normalized_document["effective_definitions"]["fields"]}
    assert fields["acme__capacity"]["label"] == "Renamed"


def test_snapshot_rejects_an_unsupported_version():
    _assert_outcome(_put(_snapshot_document(), ("schema_version",), 2), "UNSUPPORTED_VERSION")


def test_snapshot_rejects_an_unknown_kind():
    _assert_outcome(_put(_snapshot_document(), ("kind",), "itambox.type-library.unknown"), "UNSUPPORTED_KIND")


def test_snapshot_rejects_unknown_root_properties():
    _assert_outcome(_put(_snapshot_document(), ("unexpected",), 1), "UNKNOWN_PROPERTY")
