import json
from types import SimpleNamespace

import pytest

from assets.services.specification_consumers import query
from assets.services.specification_consumers.contracts import (
    MISSING,
    FieldFilter,
    FieldReference,
    canonical_field_type,
    identify_saved_references,
    parse_filter_document,
)
from assets.services.specification_consumers.exporting import (
    build_machine_export,
    build_machine_export_rows,
    human_csv_bytes,
    machine_csv_bytes,
    machine_metadata_json,
)
from assets.services.specification_consumers.semantics import (
    field_value_from_mapping,
    field_value_from_projection,
    matches_filter,
    project_source_values,
)
from extras.services.specifications.codecs import SAFE_INTEGER_MAX, SAFE_INTEGER_MIN
from extras.services.specifications.contracts import (
    SpecificationProjectionDTO,
    SpecificationProjectionEntryDTO,
)


@pytest.mark.parametrize("tenant_ids", [(), (7,)])
def test_empty_filters_still_apply_explicit_tenant_scope(monkeypatch, tenant_ids):

    source, scoped = object(), object()
    calls = []

    def apply_scope(queryset, ids, *, tenant_field):
        calls.append((queryset, ids, tenant_field))
        return scoped

    monkeypatch.setattr(query, "scope_queryset", apply_scope)
    assert query.apply_specification_filters(source, [], tenant_ids=tenant_ids) is scoped
    assert calls == [(source, tenant_ids, "tenant_id")]


def test_field_reference_is_source_qualified_and_label_free():
    reference = FieldReference(source="asset", key="memory_capacity")

    assert reference.column_id == "asset.spec.memory_capacity"
    assert FieldReference.from_column_id(reference.column_id) == reference
    with pytest.raises(ValueError):
        FieldReference.from_column_id("asset.spec.Memory Capacity")
    with pytest.raises(ValueError):
        FieldReference.from_mapping({"label": "Memory Capacity", "operator": "eq", "value": 32})


def test_filter_document_round_trips_explicit_source_and_status():
    document = {
        "filters": [
            {
                "source": "asset_type",
                "field_key": "memory_capacity",
                "operator": "gte",
                "value": "32.000",
                "status": "history",
            }
        ]
    }

    parsed = parse_filter_document(document)

    assert parsed == (
        FieldFilter(
            reference=FieldReference(source="asset_type", key="memory_capacity"),
            operator="gte",
            value="32.000",
            status="history",
        ),
    )


def test_presence_states_do_not_collapse():
    reference = FieldReference(source="asset", key="value")

    assert field_value_from_mapping(reference, {}).presence == "missing"
    assert field_value_from_mapping(reference, {"value": None}).presence == "null"
    assert field_value_from_mapping(reference, {"value": ""}).presence == "empty"
    assert field_value_from_mapping(reference, {"value": []}).presence == "empty"
    assert field_value_from_mapping(reference, {"value": 0}).presence == "value"
    assert field_value_from_mapping(reference, {"value": False}).presence == "value"


def test_current_mode_rejects_invalid_and_history_mode_keeps_status_explicit():
    reference = FieldReference(source="asset", key="memory_capacity")
    invalid = field_value_from_mapping(reference, {"memory_capacity": "not-a-number"}, status="invalid")
    historical = field_value_from_mapping(reference, {"memory_capacity": 16}, status="historical")

    current_filter = FieldFilter(reference, "gte", 32)
    history_filter = FieldFilter(reference, "gte", 8, status="history")

    assert not matches_filter(invalid, current_filter, field_type="decimal")
    assert not matches_filter(historical, current_filter, field_type="integer")
    assert matches_filter(historical, history_filter, field_type="integer")


def test_machine_export_has_presence_and_status_columns_and_keeps_null_empty():
    references = (
        FieldReference(source="asset", key="memory_capacity"),
        FieldReference(source="asset", key="note"),
    )
    result = build_machine_export(
        references,
        {
            "memory_capacity": (32, "current"),
            "note": ("", "current"),
        },
    )

    assert result.columns == (
        "asset.spec.memory_capacity.value",
        "asset.spec.memory_capacity.present",
        "asset.spec.memory_capacity.status",
        "asset.spec.note.value",
        "asset.spec.note.present",
        "asset.spec.note.status",
    )
    assert result.rows[0]["asset.spec.memory_capacity.present"] is True
    assert result.rows[0]["asset.spec.note.value"] == ""
    assert result.rows[0]["asset.spec.note.present"] is True
    assert result.metadata[0]["field_identity"] == "asset.spec.memory_capacity"

    csv_text = machine_csv_bytes(result).decode("utf-8")
    assert '"32"' in csv_text
    assert '"true"' in csv_text
    assert "current" in csv_text


def test_human_csv_formula_safety_is_separate_from_machine_interchange():
    output = human_csv_bytes(("Memory",), (("=1+1",),))

    assert "'=1+1" in output.decode("utf-8")
    assert '"=1+1"' not in output.decode("utf-8")


def test_filter_values_are_scalar_sequences_only():
    with pytest.raises(ValueError, match="scalar sequences"):
        parse_filter_document(
            {
                "filters": [
                    {
                        "source": "asset",
                        "field_key": "ports",
                        "operator": "contains_any",
                        "value": [["usb_c"]],
                    }
                ]
            }
        )


def test_multi_select_semantics_distinguish_any_and_all():
    reference = FieldReference(source="asset", key="ports")
    field = field_value_from_mapping(reference, {"ports": ["usb_c", "hdmi"]})

    assert matches_filter(field, FieldFilter(reference, "contains_any", ["vga", "hdmi"]), field_type="multi_select")
    assert not matches_filter(
        field, FieldFilter(reference, "contains_any", ["vga", "displayport"]), field_type="multi_select"
    )
    assert matches_filter(field, FieldFilter(reference, "contains_all", ["usb_c", "hdmi"]), field_type="multi_select")
    assert not matches_filter(
        field, FieldFilter(reference, "contains_all", ["usb_c", "vga"]), field_type="multi_select"
    )


def test_asset_report_export_keeps_asset_and_asset_type_values_separate():
    from types import SimpleNamespace

    from assets.reports import AssetSummaryReportProvider

    asset_reference = FieldReference(source="asset", key="memory_capacity")
    type_reference = FieldReference(source="asset_type", key="memory_capacity")
    asset = SimpleNamespace(
        pk=42,
        custom_field_data={"memory_capacity": "24.000"},
        asset_type=SimpleNamespace(custom_field_data={"memory_capacity": "16.000"}),
    )

    result = AssetSummaryReportProvider().build_specification_export([asset], (asset_reference, type_reference))

    assert result.rows[0]["asset.spec.memory_capacity.value"] == "24.000"
    assert result.rows[0]["asset_type.spec.memory_capacity.value"] == "16.000"


def test_current_composed_applicability_requires_an_active_fieldset():
    from types import SimpleNamespace

    from assets.models import Asset

    reference = FieldReference(source="asset", key="memory_capacity")
    definition = SimpleNamespace(
        key="memory_capacity",
        targets=frozenset({"asset"}),
        lifecycle="active",
        activation="composed",
    )

    condition = query._current_applicability(Asset.all_objects.all(), reference, definition)

    assert (
        "asset_type__fieldset_memberships__fieldset__lifecycle",
        "active",
    ) in condition.children


def test_postgres_cast_consumers_validate_input_before_conversion():
    from types import SimpleNamespace

    from assets.models import Asset

    queryset = Asset.all_objects.all()
    decimal_reference = FieldReference(source="asset", key="memory_capacity")
    decimal_definition = SimpleNamespace(
        key="memory_capacity",
        field_type="decimal",
        targets=frozenset({"asset"}),
        lifecycle="active",
        activation="global",
    )
    decimal_sql = str(
        query.apply_specification_filter(
            queryset,
            FieldFilter(decimal_reference, "gte", "1"),
            field_definition=decimal_definition,
        ).query
    )

    date_reference = FieldReference(source="asset", key="purchased_on")
    date_definition = SimpleNamespace(
        key="purchased_on",
        field_type="date",
        targets=frozenset({"asset"}),
        lifecycle="active",
        activation="global",
    )
    date_sql = str(
        query.apply_specification_filter(
            queryset,
            FieldFilter(date_reference, "eq", "2024-02-29"),
            field_definition=date_definition,
        ).query
    )

    assert decimal_sql.count("pg_input_is_valid") >= 1
    assert "numeric(48,12)" in decimal_sql
    assert date_sql.count("pg_input_is_valid") >= 1
    assert "to_date" in date_sql


def test_postgres_integer_consumers_use_the_shared_safe_integer_domain():
    from types import SimpleNamespace

    from assets.models import Asset

    reference = FieldReference(source="asset", key="integer_value")
    definition = SimpleNamespace(
        key="integer_value",
        field_type="integer",
        targets=frozenset({"asset"}),
        lifecycle="active",
        activation="global",
    )
    integer_sql = str(
        query.apply_specification_filter(
            Asset.all_objects.all(),
            FieldFilter(reference, "gte", 0),
            field_definition=definition,
        ).query
    )

    assert "bigint" in integer_sql
    assert str(SAFE_INTEGER_MIN) in integer_sql
    assert str(SAFE_INTEGER_MAX) in integer_sql


# ---------------------------------------------------------------------------
# Batch D: presence/status/comparison semantics, contract validation, saved
# reference inventory, and lossless export shapes for the T23 consumers.
# ---------------------------------------------------------------------------


def test_presence_matrix_distinguishes_missing_null_empty_and_value():
    reference = FieldReference(source="asset", key="value")

    assert field_value_from_mapping(reference, {}).presence == "missing"
    assert field_value_from_mapping(reference, {"value": None}).presence == "null"
    assert field_value_from_mapping(reference, {"value": ()}).presence == "empty"
    assert field_value_from_mapping(reference, {"value": [None]}).presence == "value"
    assert field_value_from_mapping(reference, {"value": "0"}).presence == "value"
    assert field_value_from_mapping(reference, {"value": ""}).status == "current"


def test_field_value_from_mapping_rejects_unsupported_status():
    reference = FieldReference(source="asset", key="value")

    with pytest.raises(ValueError, match="unsupported value status"):
        field_value_from_mapping(reference, {"value": 1}, status="archived")

    projectable = field_value_from_mapping(reference, {"value": 1}, status="invalid")
    assert projectable.status == "invalid"


def test_projection_dto_projects_only_the_matching_key_and_keeps_entry_state():
    historical = SpecificationProjectionEntryDTO(
        key="memory_capacity",
        value="32.000",
        state="historical",
        reason_codes=(),
        definition=None,
    )
    empty_note = SpecificationProjectionEntryDTO(
        key="note",
        value="",
        state="current",
        reason_codes=(),
        definition=None,
    )
    projection = SpecificationProjectionDTO(entries=(historical, empty_note), missing_required_issues=())

    projected = field_value_from_projection(FieldReference(source="asset", key="memory_capacity"), projection)
    projected_note = field_value_from_projection(FieldReference(source="asset", key="note"), projection)
    absent = field_value_from_projection(FieldReference(source="asset", key="serial"), projection)

    assert (projected.value, projected.presence, projected.status) == ("32.000", "value", "historical")
    assert (projected_note.presence, projected_note.status) == ("empty", "current")
    assert absent.value is MISSING
    assert (absent.presence, absent.status) == ("missing", "current")


def test_project_source_values_uses_only_the_explicit_source_map():
    asset_reference = FieldReference(source="asset", key="memory_capacity")
    type_reference = FieldReference(source="asset_type", key="memory_capacity")
    note_reference = FieldReference(source="asset", key="note")

    projected = project_source_values(
        {asset_reference: {"memory_capacity": "24.000"}, type_reference: {"memory_capacity": ""}},
        (asset_reference, type_reference, note_reference),
    )

    assert [(item.reference, item.presence) for item in projected] == [
        (asset_reference, "value"),
        (type_reference, "empty"),
        (note_reference, "missing"),
    ]


def test_canonical_field_type_normalizes_spellings_and_rejects_non_strings():
    assert canonical_field_type("multi-select") == "multi_select"
    assert canonical_field_type("decimal") == "decimal"
    assert canonical_field_type(None) is None
    assert canonical_field_type(7) is None


def test_decimal_equality_compares_parsed_values_and_never_equates_malformed_text():
    reference = FieldReference(source="asset", key="memory_capacity")
    text_value = field_value_from_mapping(reference, {"memory_capacity": "32.000"})
    numeric_value = field_value_from_mapping(reference, {"memory_capacity": 32})
    malformed = field_value_from_mapping(reference, {"memory_capacity": "not-a-number"})

    assert matches_filter(text_value, FieldFilter(reference, "eq", "32"), field_type="decimal")
    assert matches_filter(numeric_value, FieldFilter(reference, "eq", "32.000"), field_type="decimal")
    assert not matches_filter(text_value, FieldFilter(reference, "eq", "32.5"), field_type="decimal")
    assert matches_filter(text_value, FieldFilter(reference, "neq", "32.5"), field_type="decimal")
    assert not matches_filter(malformed, FieldFilter(reference, "eq", "not-a-number"), field_type="decimal")
    assert matches_filter(malformed, FieldFilter(reference, "neq", "not-a-number"), field_type="decimal")


def test_date_equality_requires_parsable_iso_text_on_both_sides():
    reference = FieldReference(source="asset", key="purchased_on")
    iso_value = field_value_from_mapping(reference, {"purchased_on": "2024-02-29"})
    german_text = field_value_from_mapping(reference, {"purchased_on": "29.02.2024"})

    assert matches_filter(iso_value, FieldFilter(reference, "eq", "2024-02-29"), field_type="date")
    assert not matches_filter(iso_value, FieldFilter(reference, "eq", "2024-02-30"), field_type="date")
    assert not matches_filter(german_text, FieldFilter(reference, "eq", "2024-02-29"), field_type="date")
    assert not matches_filter(iso_value, FieldFilter(reference, "eq", 2024), field_type="date")


def test_scalar_equality_stays_type_strict_for_integers_booleans_and_text():
    reference = FieldReference(source="asset", key="value")
    true_value = field_value_from_mapping(reference, {"value": True})
    zero_value = field_value_from_mapping(reference, {"value": 0})
    text_zero = field_value_from_mapping(reference, {"value": "0"})

    assert matches_filter(true_value, FieldFilter(reference, "eq", True), field_type="boolean")
    assert not matches_filter(true_value, FieldFilter(reference, "eq", 1), field_type="boolean")
    assert matches_filter(zero_value, FieldFilter(reference, "eq", 0), field_type="integer")
    assert not matches_filter(zero_value, FieldFilter(reference, "eq", False), field_type="integer")
    assert not matches_filter(text_zero, FieldFilter(reference, "eq", 0), field_type="text")
    assert matches_filter(text_zero, FieldFilter(reference, "eq", "0"), field_type="text")


def test_ordered_comparisons_compare_parsed_values_and_reject_unparsable_operands():
    reference = FieldReference(source="asset", key="value")

    def projected(value):
        return field_value_from_mapping(reference, {"value": value})

    assert matches_filter(projected(32), FieldFilter(reference, "gt", "31.5"), field_type="decimal")
    assert matches_filter(projected(32), FieldFilter(reference, "gte", 32), field_type="integer")
    assert matches_filter(projected(32), FieldFilter(reference, "lt", "33"), field_type="decimal")
    assert matches_filter(projected(32), FieldFilter(reference, "lte", 32), field_type="integer")
    assert not matches_filter(projected(32), FieldFilter(reference, "gt", "32.0"), field_type="decimal")
    assert not matches_filter(projected("not-a-number"), FieldFilter(reference, "gt", "1"), field_type="decimal")
    assert not matches_filter(projected(32), FieldFilter(reference, "gt", "not-a-number"), field_type="decimal")

    assert matches_filter(projected("2024-03-01"), FieldFilter(reference, "gt", "2024-01-01"), field_type="date")
    assert not matches_filter(projected("2024-01-01"), FieldFilter(reference, "lt", "2024-01-01"), field_type="date")
    assert not matches_filter(projected("not-a-date"), FieldFilter(reference, "lte", "2024-01-01"), field_type="date")

    assert matches_filter(projected("beta"), FieldFilter(reference, "gt", "alpha"), field_type="single-select")
    assert not matches_filter(projected(True), FieldFilter(reference, "gt", "alpha"), field_type="text")
    assert not matches_filter(projected(True), FieldFilter(reference, "gt", "alpha"), field_type="boolean")


def test_collection_operators_require_sequences_on_both_sides():
    reference = FieldReference(source="asset", key="ports")

    def projected(value):
        return field_value_from_mapping(reference, {reference.key: value})

    assert matches_filter(projected(["a", "b"]), FieldFilter(reference, "contains_any", ["b", "c"]))
    assert not matches_filter(projected(["a", "b"]), FieldFilter(reference, "contains_any", ["c"]))
    assert matches_filter(projected(["a", "b"]), FieldFilter(reference, "contains_all", ("a", "b")))
    assert not matches_filter(projected(["a"]), FieldFilter(reference, "contains_all", ("a", "b")))
    with pytest.raises(ValueError, match="JSON scalars or scalar sequences"):
        FieldFilter(reference, "contains_all", frozenset({"a"}))
    assert not matches_filter(projected("a,b"), FieldFilter(reference, "contains_any", ["a"]))
    assert not matches_filter(projected(["a"]), FieldFilter(reference, "contains_any", "a"))


def test_text_contains_is_case_insensitive_and_needs_text_on_both_sides():
    reference = FieldReference(source="asset", key="name")
    name = field_value_from_mapping(reference, {"name": "Rack Switch"})

    assert matches_filter(name, FieldFilter(reference, "contains", "rack"))
    assert matches_filter(name, FieldFilter(reference, "contains", "SWITCH"))
    assert not matches_filter(name, FieldFilter(reference, "contains", "router"))
    numeric_name = field_value_from_mapping(reference, {"name": 42})
    assert not matches_filter(numeric_name, FieldFilter(reference, "contains", "42"))
    assert not matches_filter(name, FieldFilter(reference, "contains", 42))


def test_presence_operators_and_status_guards_do_not_collapse():
    reference = FieldReference(source="asset", key="value")
    other_source = FieldReference(source="asset_type", key="value")
    missing = field_value_from_mapping(reference, {})
    null_value = field_value_from_mapping(reference, {"value": None})
    empty = field_value_from_mapping(reference, {"value": ""})
    zero = field_value_from_mapping(reference, {"value": 0})
    historical = field_value_from_mapping(reference, {"value": 8}, status="historical")

    assert matches_filter(missing, FieldFilter(reference, "is_missing"))
    assert not matches_filter(zero, FieldFilter(reference, "is_missing"))
    assert matches_filter(null_value, FieldFilter(reference, "is_null"))
    assert not matches_filter(missing, FieldFilter(reference, "is_null"))
    assert matches_filter(empty, FieldFilter(reference, "is_empty"))
    assert not matches_filter(zero, FieldFilter(reference, "is_empty"))
    assert not matches_filter(empty, FieldFilter(reference, "is_null"))

    assert matches_filter(historical, FieldFilter(reference, "gte", 8, status="history"), field_type="integer")
    assert not matches_filter(historical, FieldFilter(reference, "gte", 8), field_type="integer")
    assert not matches_filter(zero, FieldFilter(reference, "gte", 8, status="history"), field_type="integer")
    assert not matches_filter(historical, FieldFilter(other_source, "gte", 8, status="history"), field_type="integer")
    assert not matches_filter(historical, FieldFilter(reference, "gte", 8, status="history"))


def test_field_reference_rejects_non_canonical_sources_keys_and_column_ids():
    with pytest.raises(ValueError, match="source must be 'asset' or 'asset_type'"):
        FieldReference(source="device", key="memory_capacity")
    with pytest.raises(ValueError, match="stable lowercase snake_case key"):
        FieldReference(source="asset", key="MemoryCapacity")
    with pytest.raises(ValueError, match="column id must be a string"):
        FieldReference.from_column_id(7)
    with pytest.raises(ValueError, match="column id must use"):
        FieldReference.from_column_id("device.spec.memory_capacity")
    with pytest.raises(ValueError, match="field reference must be an object"):
        FieldReference.from_mapping([("source", "asset")])
    with pytest.raises(ValueError, match="requires source and field_key"):
        FieldReference.from_mapping({"field_key": "memory_capacity"})
    with pytest.raises(ValueError, match="must not use labels"):
        FieldReference.from_mapping({"source": "asset", "field_key": "memory_capacity", "label": "Memory"})


def test_field_reference_mapping_round_trip_stays_source_qualified():
    reference = FieldReference(source="asset_type", key="memory_capacity")

    assert reference.to_mapping() == {"source": "asset_type", "field_key": "memory_capacity"}
    assert FieldReference.from_mapping(reference.to_mapping()) == reference
    assert FieldReference.from_column_id("asset_type.spec.memory_capacity") == reference


def test_field_filter_validation_matrix():
    reference = FieldReference(source="asset", key="memory_capacity")

    with pytest.raises(TypeError, match="reference must be a FieldReference"):
        FieldFilter("asset.spec.memory_capacity", "eq", 1)
    with pytest.raises(ValueError, match="unsupported specification filter operator"):
        FieldFilter(reference, "like", 1)
    with pytest.raises(ValueError, match="unsupported specification filter status"):
        FieldFilter(reference, "eq", 1, status="archived")
    with pytest.raises(ValueError, match="does not accept a value"):
        FieldFilter(reference, "is_null", None)
    with pytest.raises(ValueError, match="requires a value"):
        FieldFilter(reference, "eq")
    with pytest.raises(ValueError, match="filter values cannot contain mappings"):
        FieldFilter(reference, "contains_any", [{"source": "asset"}])
    with pytest.raises(ValueError, match="JSON scalars or scalar sequences"):
        FieldFilter(reference, "contains_all", {"a": 1})


def test_field_filter_mapping_and_status_state_are_explicit():
    reference = FieldReference(source="asset", key="memory_capacity")
    presence = FieldFilter(reference, "is_missing")
    history = FieldFilter(reference, "gte", 8, status="history")

    assert presence.to_mapping() == {
        "source": "asset",
        "field_key": "memory_capacity",
        "operator": "is_missing",
        "status": "current",
    }
    assert "value" not in presence.to_mapping()
    assert history.to_mapping()["value"] == 8
    assert history.status_state == "historical"
    assert FieldFilter(reference, "eq", 1).status_state == "current"
    assert FieldFilter(reference, "eq", 1, status="unknown").status_state == "unknown"
    assert parse_filter_document([presence.to_mapping()]) == (presence,)


def test_filter_document_parser_accepts_sequence_and_json_text_forms():
    document = [{"source": "asset", "field_key": "memory_capacity", "operator": "is_missing"}]
    parsed = parse_filter_document(document)

    assert parsed == (
        FieldFilter(
            reference=FieldReference(source="asset", key="memory_capacity"),
            operator="is_missing",
            value=MISSING,
            status="current",
        ),
    )
    assert parse_filter_document(json.dumps(document)) == parsed
    assert parse_filter_document({"filters": ()}) == ()


def test_filter_document_parser_rejects_unrepairable_documents():
    key = {"source": "asset", "field_key": "memory_capacity", "operator": "eq", "value": 1}

    with pytest.raises(ValueError, match="document is not valid JSON"):
        parse_filter_document("[")
    with pytest.raises(ValueError, match="unknown properties"):
        parse_filter_document({"filters": [], "columns": ["asset_tag"]})
    with pytest.raises(ValueError, match="requires a filters sequence"):
        parse_filter_document({"filters": "memory_capacity"})
    with pytest.raises(ValueError, match="must be an object"):
        parse_filter_document(["memory_capacity"])
    with pytest.raises(ValueError, match="has unknown properties"):
        parse_filter_document([{**key, "label": "Memory Capacity"}])
    with pytest.raises(ValueError, match="has unknown properties"):
        parse_filter_document([{**key, "column": "asset.spec.memory_capacity"}])
    with pytest.raises(ValueError, match="requires an operator"):
        parse_filter_document([{"source": "asset", "field_key": "memory_capacity", "value": 1}])
    with pytest.raises(ValueError, match="unsupported specification filter operator"):
        parse_filter_document([{**key, "operator": "like"}])
    with pytest.raises(ValueError, match="unsupported specification filter status"):
        parse_filter_document([{**key, "status": "archived"}])


def test_saved_reference_inventory_classifies_canonical_historical_unresolved_and_legacy():
    current_reference = FieldReference(source="asset", key="memory_capacity")
    historical_reference = FieldReference(source="asset", key="old_capacity")

    document = {
        "views": [
            {
                "filters": [
                    {"source": "asset", "field_key": "memory_capacity", "operator": "eq", "value": 32},
                    {"source": "asset", "field_key": "old_capacity", "operator": "eq", "value": 16},
                    {"source": "asset_type", "field_key": "memory_capacity", "operator": "eq", "value": 8},
                    {"label": "Memory Capacity", "operator": "eq", "value": 32},
                    {"operator": "eq", "value": 32},
                ]
            }
        ]
    }

    impacts = identify_saved_references(
        document,
        known_references={current_reference: "current", historical_reference: "historical"},
    )

    assert [(impact.path, impact.status) for impact in impacts] == [
        (("views", "0", "filters", "0"), "valid"),
        (("views", "0", "filters", "1"), "historical"),
        (("views", "0", "filters", "2"), "unresolved"),
        (("views", "0", "filters", "3"), "legacy"),
    ]
    assert impacts[0].reference == current_reference
    assert impacts[3].reference is None
    assert impacts[3].reason


def test_saved_reference_inventory_reports_unknown_known_status_as_legacy():
    reference = FieldReference(source="asset", key="memory_capacity")

    impacts = identify_saved_references(
        {"filters": [{"source": "asset", "field_key": "memory_capacity", "operator": "eq", "value": 1}]},
        known_references={reference: "deprecated"},
    )

    assert [(impact.reference, impact.status, impact.reason) for impact in impacts] == [
        (reference, "legacy", "canonical reference")
    ]


def test_saved_reference_inventory_ignores_documents_without_filters():
    assert identify_saved_references({"name": "Saved view", "columns": ["asset_tag"]}) == ()
    assert identify_saved_references(None) == ()
    assert identify_saved_references([]) == ()


def test_machine_export_rejects_ambiguous_bare_keys_duplicates_and_unknown_shapes():
    asset_reference = FieldReference(source="asset", key="memory_capacity")
    type_reference = FieldReference(source="asset_type", key="memory_capacity")

    with pytest.raises(ValueError, match="ambiguous bare specification key"):
        build_machine_export((asset_reference,), {type_reference: (16, "current"), "memory_capacity": 8})
    with pytest.raises(ValueError, match="references must be unique"):
        build_machine_export((asset_reference, asset_reference), {})
    with pytest.raises(ValueError, match="does not match export column"):
        build_machine_export(
            (asset_reference,),
            {asset_reference: field_value_from_mapping(type_reference, {"memory_capacity": 1})},
        )
    with pytest.raises(ValueError, match="ProjectedFieldValue or"):
        build_machine_export((asset_reference,), {asset_reference: 32})


def test_machine_export_accepts_column_id_pairs_and_multi_row_forms():
    reference = FieldReference(source="asset", key="memory_capacity")
    columns = (
        "asset.spec.memory_capacity.value",
        "asset.spec.memory_capacity.present",
        "asset.spec.memory_capacity.status",
    )

    by_column_id = build_machine_export((reference,), {reference.column_id: (32, "current")})
    assert by_column_id.rows[0]["asset.spec.memory_capacity.value"] == 32
    assert by_column_id.rows[0]["asset.spec.memory_capacity.present"] is True
    assert by_column_id.rows[0]["asset.spec.memory_capacity.status"] == "current"

    rows = build_machine_export_rows((reference,), [{reference: (32, "current")}, {reference: (16, "historical")}])
    assert rows.columns == columns
    assert [row["asset.spec.memory_capacity.value"] for row in rows.rows] == [32, 16]
    assert [row["asset.spec.memory_capacity.status"] for row in rows.rows] == ["current", "historical"]

    empty = build_machine_export_rows((reference,), [])
    assert empty.columns == columns
    assert empty.rows == ()
    assert empty.metadata == by_column_id.metadata

    metadata_json = machine_metadata_json(by_column_id).decode("utf-8")
    assert "current|historical|invalid|unknown" in metadata_json
    assert "asset.spec.memory_capacity" in metadata_json


def test_machine_export_metadata_carries_definition_label_and_unit():
    reference = FieldReference(source="asset", key="memory_capacity")
    definition = SimpleNamespace(label="Memory Capacity", canonical_unit="GB")

    with_definition = build_machine_export(
        (reference,), {reference: (32, "current")}, definitions={reference: definition}
    )
    without_definition = build_machine_export((reference,), {})

    assert with_definition.metadata[0]["label"] == "Memory Capacity"
    assert with_definition.metadata[0]["unit"] == "GB"
    assert without_definition.metadata[0]["label"] is None
    assert without_definition.metadata[0]["unit"] is None
    assert without_definition.metadata[0]["field_identity"] == "asset.spec.memory_capacity"
    assert without_definition.rows[0]["asset.spec.memory_capacity.value"] is None
    assert without_definition.rows[0]["asset.spec.memory_capacity.present"] is False


# ---------------------------------------------------------------------------
# Batch D: query compiler contracts - scope, source qualification, status
# policy, operator/type validation, and definition applicability.
# ---------------------------------------------------------------------------


def _asset_queryset():
    from assets.models import Asset

    return Asset.all_objects.all()


def _asset_type_queryset():
    from assets.models import AssetType

    return AssetType.objects.all()


def _definition(key, field_type, *, targets=("asset",), lifecycle="active", activation="global"):
    return SimpleNamespace(
        key=key,
        field_type=field_type,
        targets=frozenset(targets),
        lifecycle=lifecycle,
        activation=activation,
    )


def _is_provably_empty(queryset):
    from django.core.exceptions import EmptyResultSet

    if queryset.query.is_empty():
        return True
    try:
        str(queryset.query)
    except EmptyResultSet:
        return True
    return False


@pytest.mark.parametrize("tenant_ids", [(0,), (-1,), (1.5,), ("1",), (None,)])
def test_scope_queryset_rejects_tenant_ids_that_are_not_positive_integers(tenant_ids):
    with pytest.raises(query.SpecificationQueryError, match="positive integers"):
        query.scope_queryset(_asset_queryset(), tenant_ids)


def test_scope_queryset_never_turns_empty_authorization_into_a_global_query():
    scoped = query.scope_queryset(_asset_queryset(), ())

    assert scoped.query.is_empty()


def test_scope_queryset_applies_tenant_criteria_and_active_tenant_guard():
    sql = str(query.scope_queryset(_asset_queryset(), (7, 8)).query)

    assert "tenant_id" in sql
    assert "IN (7, 8)" in sql
    assert "deleted_at" in sql
    assert "IS NULL" in sql


def test_scope_queryset_supports_an_explicit_tenant_field_without_the_tenant_join():
    sql = str(query.scope_queryset(_asset_queryset(), (7,), tenant_field="cost_center_id").query)

    assert "cost_center_id" in sql
    assert "organization_tenant" not in sql
    assert "JOIN" not in sql


def test_query_target_and_json_path_reject_cross_source_consumption():
    from organization.models import Tenant

    asset_reference = FieldReference(source="asset", key="memory_capacity")
    type_reference = FieldReference(source="asset_type", key="memory_capacity")

    with pytest.raises(query.SpecificationQueryError, match="support Asset and AssetType"):
        query.apply_specification_filter(Tenant.objects.all(), FieldFilter(asset_reference, "eq", "1"))
    with pytest.raises(query.SpecificationQueryError, match="Asset source cannot be applied to an AssetType"):
        query.apply_specification_filter(_asset_type_queryset(), FieldFilter(asset_reference, "eq", "1"))
    with pytest.raises(query.SpecificationQueryError, match="field definition key does not match"):
        query.apply_specification_filter(
            _asset_queryset(),
            FieldFilter(asset_reference, "eq", "1"),
            field_definition=_definition("other_key", "decimal"),
        )

    assert query._json_path(_asset_queryset(), asset_reference) == "custom_field_data"
    assert query._json_path(_asset_queryset(), type_reference) == "asset_type__custom_field_data"
    assert query._json_path(_asset_type_queryset(), type_reference) == "custom_field_data"


def test_reference_outside_definition_targets_returns_no_rows():
    type_reference = FieldReference(source="asset_type", key="memory_capacity")
    asset_only = _definition("memory_capacity", "decimal", targets=("asset",))

    scoped = query.apply_specification_filter(
        _asset_queryset(),
        FieldFilter(type_reference, "eq", "16.000"),
        field_definition=asset_only,
    )

    assert scoped.query.is_empty()


def test_unknown_status_needs_a_definition_and_otherwise_only_checks_presence():
    reference = FieldReference(source="asset", key="memory_capacity")

    with_definition = query.apply_specification_filter(
        _asset_queryset(),
        FieldFilter(reference, "eq", "1", status="unknown"),
        field_definition=_definition("memory_capacity", "decimal"),
    )
    without_definition = query.apply_specification_filter(
        _asset_queryset(),
        FieldFilter(reference, "eq", "1", status="unknown"),
    )
    without_definition_sql = str(without_definition.query)

    assert with_definition.query.is_empty()
    assert not without_definition.query.is_empty()
    assert "pg_input_is_valid" not in without_definition_sql
    assert "custom_field_data" in without_definition_sql


def test_invalid_status_needs_a_definition_and_filters_rows_failing_the_type_guard():
    reference = FieldReference(source="asset", key="memory_capacity")

    without_definition = query.apply_specification_filter(
        _asset_queryset(),
        FieldFilter(reference, "eq", "1", status="invalid"),
    )
    with_definition = query.apply_specification_filter(
        _asset_queryset(),
        FieldFilter(reference, "eq", "1", status="invalid"),
        field_definition=_definition("memory_capacity", "decimal"),
    )
    with_definition_sql = str(with_definition.query)

    assert without_definition.query.is_empty()
    assert not with_definition.query.is_empty()
    assert "jsonb_typeof" in with_definition_sql
    assert "NOT" in with_definition_sql


def test_history_status_requires_knowable_applicability_and_negates_it():
    reference = FieldReference(source="asset", key="memory_capacity")
    global_definition = _definition("memory_capacity", "decimal", activation="global")
    composed_definition = _definition("memory_capacity", "decimal", activation="composed")

    without_definition = query.apply_specification_filter(
        _asset_queryset(),
        FieldFilter(reference, "gte", 1, status="history"),
    )
    with_global = query.apply_specification_filter(
        _asset_queryset(),
        FieldFilter(reference, "gte", 1, status="history"),
        field_definition=global_definition,
    )
    with_composed = query.apply_specification_filter(
        _asset_queryset(),
        FieldFilter(reference, "gte", 1, status="history"),
        field_definition=composed_definition,
    )

    assert without_definition.query.is_empty()
    assert with_global.query.is_empty()
    assert not with_composed.query.is_empty()
    assert "fieldset" in str(with_composed.query)


def test_current_status_keeps_the_scope_for_an_actively_composed_reference():
    reference = FieldReference(source="asset", key="memory_capacity")
    definition = _definition("memory_capacity", "decimal", activation="global")

    scoped = query.apply_specification_filter(
        _asset_queryset(),
        FieldFilter(reference, "eq", "24.000"),
        field_definition=definition,
    )

    assert not scoped.query.is_empty()
    assert "fieldset" not in str(scoped.query)


def test_retired_definition_makes_the_current_reference_inapplicable():
    reference = FieldReference(source="asset", key="memory_capacity")

    scoped = query.apply_specification_filter(
        _asset_queryset(),
        FieldFilter(reference, "eq", "24.000"),
        field_definition=_definition("memory_capacity", "decimal", lifecycle="deprecated"),
    )

    assert _is_provably_empty(scoped)


def test_presence_conditions_compile_three_distinct_predicates():
    reference = FieldReference(source="asset", key="memory_capacity")

    missing_sql = str(query.apply_specification_filter(_asset_queryset(), FieldFilter(reference, "is_missing")).query)
    null_sql = str(query.apply_specification_filter(_asset_queryset(), FieldFilter(reference, "is_null")).query)
    empty_sql = str(query.apply_specification_filter(_asset_queryset(), FieldFilter(reference, "is_empty")).query)

    assert "NOT" in missing_sql
    assert "= null" in null_sql
    assert "jsonb_array_length" in empty_sql
    assert len({missing_sql, null_sql, empty_sql}) == 3


def test_typed_conditions_compile_negations_and_json_containment():
    def sql(operator, value, field_type, key):
        spec = FieldFilter(FieldReference(source="asset", key=key), operator, value)
        return str(
            query.apply_specification_filter(
                _asset_queryset(),
                spec,
                field_definition=_definition(key, field_type),
            ).query
        )

    boolean_sql = sql("neq", True, "boolean", "is_active")
    text_sql = sql("neq", "Rack", "text", "name")
    date_sql = sql("neq", "2024-02-29", "date", "purchased_on")
    multi_sql = sql("contains_any", ["usb_c"], "multi_select", "ports")
    integer_sql = sql("neq", 3, "integer", "count")

    assert "= true" in boolean_sql
    assert "NOT" in boolean_sql
    assert "= Rack" in text_sql
    assert "NOT" in text_sql
    assert "to_date" in date_sql
    assert "= 2024-02-29" in date_sql
    assert "@>" in multi_sql
    assert "jsonb_typeof" in multi_sql
    assert "bigint" in integer_sql


def test_operator_and_type_validation_rejects_unsafe_combinations():
    def apply(operator, value, field_type, key="value"):
        spec = FieldFilter(FieldReference(source="asset", key=key), operator, value)
        return query.apply_specification_filter(
            _asset_queryset(),
            spec,
            field_definition=_definition(key, field_type),
        )

    with pytest.raises(query.SpecificationQueryError, match="ISO date string"):
        apply("eq", 2024, "date")
    with pytest.raises(query.SpecificationQueryError, match="valid ISO date"):
        apply("eq", "2024-02-30", "date")
    with pytest.raises(query.SpecificationQueryError, match="do not accept booleans"):
        apply("gte", True, "decimal")
    with pytest.raises(query.SpecificationQueryError, match="require a decimal value"):
        apply("gte", "not-a-number", "decimal")
    with pytest.raises(query.SpecificationQueryError, match="requires a non-empty sequence"):
        apply("contains_any", [], "multi_select")
    with pytest.raises(query.SpecificationQueryError, match="requires a multi-select definition"):
        apply("contains_any", ["a"], "text")
    with pytest.raises(query.SpecificationQueryError, match="text contains requires a string"):
        apply("contains", 42, "text")
    with pytest.raises(query.SpecificationQueryError, match="strict equality only"):
        apply("gte", True, "boolean")
    with pytest.raises(query.SpecificationQueryError, match="strict equality only"):
        apply("eq", "true", "boolean")
    with pytest.raises(query.SpecificationQueryError, match="ordered comparisons require integer, decimal, or date"):
        apply("gt", "a", "text")
    with pytest.raises(query.SpecificationQueryError, match="require a string"):
        apply("eq", 42, "text")


def test_field_type_falls_back_to_operator_and_value_evidence_without_a_definition():
    reference = FieldReference(source="asset", key="memory_capacity")

    def sql(filter_spec):
        return str(query.apply_specification_filter(_asset_queryset(), filter_spec).query)

    integer_sql = sql(FieldFilter(reference, "gte", 5))
    decimal_sql = sql(FieldFilter(reference, "gte", "5.5"))
    collection_sql = sql(FieldFilter(reference, "contains_any", ["a"]))
    text_sql = sql(FieldFilter(reference, "eq", "24.000"))

    assert "bigint" in integer_sql
    assert "numeric(48,12)" in decimal_sql
    assert "@>" in collection_sql
    assert "= 24.000" in text_sql


def test_apply_specification_filters_scopes_once_then_applies_every_filter():
    reference = FieldReference(source="asset", key="memory_capacity")
    definition = _definition("memory_capacity", "decimal")

    scoped = query.apply_specification_filters(
        _asset_queryset(),
        (FieldFilter(reference, "gte", "8"), FieldFilter(reference, "lte", "64")),
        tenant_ids=(11,),
        definitions={reference: definition},
    )
    sql = str(scoped.query)

    assert "IN (11)" in sql
    assert "numeric(48,12)" in sql
    assert "pg_input_is_valid" in sql
    assert "@>" not in sql


def test_apply_specification_filter_requires_a_field_filter_instance():
    with pytest.raises(TypeError, match="filter_spec must be a FieldFilter"):
        query.apply_specification_filter(_asset_queryset(), {"source": "asset", "field_key": "memory_capacity"})


def test_value_aliases_are_stable_and_distinguish_the_two_sources():
    asset_reference = FieldReference(source="asset", key="memory_capacity")
    type_reference = FieldReference(source="asset_type", key="memory_capacity")

    aliases = query._aliases(asset_reference, "custom_field_data")
    annotated, text_alias, type_alias = query._annotate_value(_asset_queryset(), asset_reference, "custom_field_data")

    assert aliases == query._aliases(asset_reference, "custom_field_data")
    assert aliases != query._aliases(type_reference, "asset_type__custom_field_data")
    assert all(alias.startswith("_t23_spec_") for alias in aliases)
    assert (text_alias, type_alias) == aliases
    assert text_alias in annotated.query.annotations
    assert type_alias in annotated.query.annotations
