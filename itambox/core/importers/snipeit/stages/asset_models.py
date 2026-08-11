"""Stage for Snipe-IT asset models."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass

from django.apps import apps
from django.db import transaction

from core.importers.snipeit.common import _nested_id
from core.importers.snipeit.contracts import ImportContext, Outcome, StageResult


@dataclass(frozen=True)
class AssetModelDependencies:
    manufacturers: Mapping[int, object]
    categories: Mapping[int, object]
    fieldsets: Mapping[int, object]
    asset_models: MutableMapping[int, object]


class AssetModelImporter:
    key = "models"

    def __init__(self, context: ImportContext, dependencies: AssetModelDependencies) -> None:
        self.context = context
        self.dependencies = dependencies

    def _upsert(self, model, row) -> tuple[object, Outcome]:
        source_id = row["id"]
        model_name = (row.get("name") or "").strip() or f"Model {source_id}"
        manufacturer = self.dependencies.manufacturers.get(_nested_id(row.get("manufacturer")))
        category = self.dependencies.categories.get(_nested_id(row.get("category")))
        fieldset = self.dependencies.fieldsets.get(_nested_id(row.get("fieldset")))
        raw_eol_months = row.get("eol") or None
        try:
            eol_months = int(raw_eol_months) if raw_eol_months else None
        # broad except: boundary-isolation: optional child rows may degrade without discarding the parent item
        except (TypeError, ValueError):
            eol_months = None
        part_number = (row.get("model_number") or "")[:100]
        defaults = {
            "model": model_name,
            "manufacturer": manufacturer,
            "category": category,
            "custom_fieldset": fieldset,
            "eol_months": eol_months,
            "part_number": part_number,
            "custom_field_data": {"snipeit_id": str(source_id)},
        }
        obj = model.all_objects.filter(custom_field_data__snipeit_id=str(source_id)).first()
        if not obj:
            obj = model.all_objects.filter(model=model_name, manufacturer=manufacturer).first()
        if obj:
            if not self.context.update:
                return obj, "skipped"
            if not self.context.dry_run:
                for field, value in defaults.items():
                    setattr(obj, field, value)
                obj.save()
            return obj, "updated"
        if not self.context.dry_run:
            obj = model.objects.create(**defaults)
        else:
            obj = model(id=-source_id, model=model_name, manufacturer=manufacturer)
        return obj, "created"

    def run(self) -> StageResult:
        model = apps.get_model("assets", "AssetType")
        result = StageResult(self.key)
        self.context.reporter.start(result)
        for row in self.context.client.get_all("/api/v1/models"):
            source_id = row["id"]
            try:
                with transaction.atomic():
                    obj, outcome = self._upsert(model, row)
                self.dependencies.asset_models[source_id] = obj
                result.counts.record(outcome)
            # broad except: task-isolation: one remote row must not abort the reviewed import batch
            except Exception as exc:
                self.context.reporter.row_failure(result, "models.persist", exc)
        self.context.reporter.finish(result)
        return result
