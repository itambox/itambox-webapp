from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass

from django.apps import apps
from django.db import transaction

from core.importers.snipeit.common import (
    _MAINTENANCE_TYPE_MAP,
    _nested_id,
    _nested_str,
    _parse_date,
    _parse_decimal,
)
from core.importers.snipeit.contracts import ImportContext, StageResult


@dataclass(frozen=True)
class MaintenanceDependencies:
    assets: Mapping
    suppliers: Mapping


class MaintenanceImporter:
    key = "maintenances"

    def __init__(self, context: ImportContext, dependencies: MaintenanceDependencies) -> None:
        self.context = context
        self.dependencies = dependencies

    def run(self) -> StageResult:
        AssetMaintenance = apps.get_model("assets", "AssetMaintenance")
        result = StageResult(self.key)
        self.context.reporter.start(result)

        for row in self.context.client.get_all("/api/v1/maintenances"):
            asset = self.dependencies.assets.get(_nested_id(row.get("asset")))
            if not asset:
                result.counts.skipped += 1
                continue

            raw_type = (row.get("asset_maintenance_type") or "maintenance").lower()
            maintenance_type = _MAINTENANCE_TYPE_MAP.get(raw_type, "repair")
            completion_raw = _nested_str(row.get("completion_date"), "date") or row.get("completion_date")
            if isinstance(completion_raw, dict):
                completion_raw = completion_raw.get("date")
            start_raw = _nested_str(row.get("start_date"), "date") or row.get("start_date")
            if isinstance(start_raw, dict):
                start_raw = start_raw.get("date")
            start_date = _parse_date(start_raw) or datetime.date.today()
            completion_date = _parse_date(completion_raw)
            status = "completed" if completion_date else "scheduled"
            supplier = self.dependencies.suppliers.get(_nested_id(row.get("supplier")))
            cost = _parse_decimal(row.get("cost"))
            notes = row.get("notes") or ""

            try:
                with transaction.atomic():
                    if not self.context.dry_run and asset.pk and asset.pk > 0:
                        obj = AssetMaintenance.all_objects.filter(
                            asset=asset,
                            start_date=start_date,
                            maintenance_type=maintenance_type,
                        ).first()
                        if obj:
                            if not self.context.update:
                                result.counts.skipped += 1
                                continue
                            obj.maintenance_type = maintenance_type
                            obj.status = status
                            obj.completion_date = completion_date
                            obj.cost = cost
                            obj.notes = notes
                            obj.supplier = supplier
                            obj.save()
                            result.counts.updated += 1
                            continue
                        AssetMaintenance.objects.create(
                            asset=asset,
                            maintenance_type=maintenance_type,
                            status=status,
                            start_date=start_date,
                            completion_date=completion_date,
                            cost=cost,
                            notes=notes,
                            supplier=supplier,
                        )
                    result.counts.created += 1
            except Exception as exc:
                self.context.reporter.row_failure(result, "maintenances.persist", exc)

        self.context.reporter.finish(result)
        return result
