"""Lossless machine and human report/export helpers for specifications."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from core.csv_utils import csv_safe

from .contracts import MISSING, FieldReference
from .semantics import ProjectedFieldValue, field_value_from_mapping


@dataclass(frozen=True)
class MachineExportResult:
    columns: tuple[str, ...]
    rows: tuple[Mapping[str, object], ...]
    metadata: tuple[Mapping[str, object], ...]


def _metadata_for_reference(
    reference: FieldReference,
    definition: object | None = None,
) -> dict[str, object]:
    return {
        "field_identity": reference.column_id,
        "source": reference.source,
        "field_key": reference.key,
        "label": getattr(definition, "label", None),
        "unit": getattr(definition, "canonical_unit", None),
        "status_policy": "current|historical|invalid|unknown",
        "presence_policy": "present distinguishes missing from JSON null/empty",
    }


def _value_for_reference(values: Mapping[object, object], reference: FieldReference) -> ProjectedFieldValue:
    candidate = values.get(reference, MISSING)
    if candidate is MISSING:
        candidate = values.get(reference.column_id, MISSING)
    if candidate is MISSING and reference.key in values:
        # A bare key is accepted only for a one-source row. Callers with more
        # than one source must use FieldReference/column_id keys to avoid an
        # Asset-vs-Type fallback.
        if any(isinstance(key, FieldReference) and key.key == reference.key and key != reference for key in values):
            raise ValueError(f"ambiguous bare specification key: {reference.key}")
        candidate = values[reference.key]
    if isinstance(candidate, ProjectedFieldValue):
        if candidate.reference != reference:
            raise ValueError("projected value reference does not match export column")
        return candidate
    if candidate is MISSING:
        return field_value_from_mapping(reference, {})
    if isinstance(candidate, tuple) and len(candidate) == 2:
        value, status = candidate
        return field_value_from_mapping(reference, {reference.key: value}, status=status)
    raise ValueError("export values must be ProjectedFieldValue or (value, status) pairs")


def build_machine_export(
    references: Sequence[FieldReference],
    values: Mapping[object, object],
    *,
    definitions: Mapping[FieldReference, object] | None = None,
) -> MachineExportResult:
    """Build one lossless machine row with value, presence, and status columns."""

    refs = tuple(references)
    if len(set(refs)) != len(refs):
        raise ValueError("machine export references must be unique")
    columns: list[str] = []
    row: dict[str, object] = {}
    metadata: list[Mapping[str, object]] = []
    for reference in refs:
        projected = _value_for_reference(values, reference)
        prefix = reference.column_id
        columns.extend((f"{prefix}.value", f"{prefix}.present", f"{prefix}.status"))
        row[f"{prefix}.value"] = None if projected.value is MISSING else projected.value
        row[f"{prefix}.present"] = projected.presence != "missing"
        row[f"{prefix}.status"] = projected.status
        metadata.append(_metadata_for_reference(reference, (definitions or {}).get(reference)))
    return MachineExportResult(tuple(columns), (row,), tuple(metadata))


def build_machine_export_rows(
    references: Sequence[FieldReference],
    rows: Sequence[Mapping[object, object]],
    *,
    definitions: Mapping[FieldReference, object] | None = None,
) -> MachineExportResult:
    """Build a machine export for multiple records without changing identities."""

    first = build_machine_export(references, rows[0] if rows else {}, definitions=definitions)
    if not rows:
        return MachineExportResult(first.columns, (), first.metadata)
    materialized = [first.rows[0]]
    for row in rows[1:]:
        materialized.append(build_machine_export(references, row, definitions=definitions).rows[0])
    return MachineExportResult(first.columns, tuple(materialized), first.metadata)


def _json_cell(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def machine_csv_bytes(result: MachineExportResult) -> bytes:
    """Serialize machine rows with quoted JSON literals and no display escaping."""

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n", quoting=csv.QUOTE_ALL)
    writer.writerow(result.columns)
    for row in result.rows:
        writer.writerow(
            _json_cell(row[column]) if column.endswith(".value") else str(row[column]).lower()
            for column in result.columns
        )
    return output.getvalue().encode("utf-8")


def machine_metadata_json(result: MachineExportResult) -> bytes:
    """Serialize the identity/unit/status companion for a machine export."""

    return json.dumps(
        {"columns": result.metadata, "status_policy": "current|historical|invalid|unknown"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def human_csv_bytes(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> bytes:
    """Serialize display CSV with spreadsheet-formula protection.

    This is intentionally a separate path from :func:`machine_csv_bytes`;
    human labels and formula escaping are not a lossless library interchange
    format.
    """

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(tuple(str(header) for header in headers))
    for row in rows:
        writer.writerow(csv_safe(value) for value in row)
    return output.getvalue().encode("utf-8")
