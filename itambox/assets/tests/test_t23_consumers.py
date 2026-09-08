import pytest

from assets.services.specification_consumers import query
from assets.services.specification_consumers.contracts import (
    FieldFilter,
    FieldReference,
    parse_filter_document,
)
from assets.services.specification_consumers.exporting import (
    build_machine_export,
    human_csv_bytes,
    machine_csv_bytes,
)
from assets.services.specification_consumers.semantics import (
    field_value_from_mapping,
    matches_filter,
)
from extras.services.specifications.codecs import SAFE_INTEGER_MAX, SAFE_INTEGER_MIN


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
