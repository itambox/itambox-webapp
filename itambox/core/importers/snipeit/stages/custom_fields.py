"""Stages for Snipe-IT custom fields and fieldsets."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass

from django.apps import apps
from django.db import transaction

from core.importers.snipeit.common import _FIELD_FORMAT_MAP, _clean_field_name
from core.importers.snipeit.contracts import ImportContext, Outcome, StageResult


@dataclass(frozen=True)
class CustomFieldDependencies:
    custom_fields: MutableMapping[str, object]


@dataclass(frozen=True)
class FieldsetDependencies:
    custom_fields: Mapping[str, object]
    fieldsets: MutableMapping[int, object]


class CustomFieldImporter:
    key = "fields"

    def __init__(self, context: ImportContext, dependencies: CustomFieldDependencies) -> None:
        self.context = context
        self.dependencies = dependencies

    def _upsert(self, model, asset_ct, row) -> tuple[object, Outcome]:
        source_id = row["id"]
        db_column = row.get("db_column_name") or ""
        raw_name = _clean_field_name(db_column) if db_column else f"snipeit_field_{source_id}"
        label = (row.get("name") or raw_name).strip()[:100]
        fmt = (row.get("format") or row.get("type") or "TEXT").upper()
        field_type = _FIELD_FORMAT_MAP.get(fmt, "text")
        raw_choices = row.get("field_values") or ""
        choices = "\n".join(value.strip() for value in raw_choices.split("\n") if value.strip()) if raw_choices else ""
        obj = model.all_objects.filter(name=raw_name).first()
        if obj:
            if not self.context.update:
                return obj, "skipped"
            if not self.context.dry_run:
                obj.label = label
                obj.field_type = field_type
                if choices:
                    obj.choices = choices
                obj.save(update_fields=["label", "field_type", "choices"])
            return obj, "updated"
        if not self.context.dry_run:
            obj = model.objects.create(name=raw_name, label=label, field_type=field_type, choices=choices)
            obj.object_types.add(asset_ct)
        else:
            obj = model(id=-source_id, name=raw_name, label=label, field_type=field_type)
        return obj, "created"

    def run(self) -> StageResult:
        model = apps.get_model("extras", "CustomField")
        asset_model = apps.get_model("assets", "Asset")
        content_type_model = apps.get_model("contenttypes", "ContentType")
        asset_ct = content_type_model.objects.get_for_model(asset_model)
        result = StageResult(self.key)
        self.context.reporter.start(result)
        for row in self.context.client.get_all("/api/v1/fields"):
            try:
                with transaction.atomic():
                    obj, outcome = self._upsert(model, asset_ct, row)
                db_column = row.get("db_column_name") or ""
                self.dependencies.custom_fields[db_column] = obj
                result.counts.record(outcome)
            except Exception as exc:
                self.context.reporter.row_failure(result, "fields.persist", exc)
        self.context.reporter.finish(result)
        return result


class FieldsetImporter:
    key = "fieldsets"

    def __init__(self, context: ImportContext, dependencies: FieldsetDependencies) -> None:
        self.context = context
        self.dependencies = dependencies

    def _upsert(self, model, row) -> tuple[object, Outcome]:
        source_id = row["id"]
        name = (row.get("name") or "").strip() or f"Fieldset {source_id}"
        obj = model.all_objects.filter(name=name).first()
        if obj:
            if not self.context.update:
                return obj, "skipped"
            return obj, "updated"
        if not self.context.dry_run:
            obj = model.objects.create(name=name)
            field_objects = [
                self.dependencies.custom_fields[db_column]
                for row2 in (row.get("fields", {}).get("rows") or [])
                if (db_column := row2.get("db_column_name")) and db_column in self.dependencies.custom_fields
            ]
            if field_objects:
                obj.fields.set(field_objects)
        else:
            obj = model(id=-source_id, name=name)
        return obj, "created"

    def run(self) -> StageResult:
        model = apps.get_model("extras", "CustomFieldset")
        result = StageResult(self.key)
        self.context.reporter.start(result)
        for row in self.context.client.get_all("/api/v1/fieldsets"):
            source_id = row["id"]
            try:
                with transaction.atomic():
                    obj, outcome = self._upsert(model, row)
                self.dependencies.fieldsets[source_id] = obj
                result.counts.record(outcome)
            except Exception as exc:
                self.context.reporter.row_failure(result, "fieldsets.persist", exc)
        self.context.reporter.finish(result)
        return result
