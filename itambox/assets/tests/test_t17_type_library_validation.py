"""DB-free T17 tests for library parsing, graph validation, and normalization."""

from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256

import pytest
import rfc8785

from assets.services.type_library_validation import (
    InstalledDependency,
    LibraryValidationError,
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
