from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass

from dateutil.relativedelta import relativedelta
from django.apps import apps
from django.db import transaction

from core.importers.snipeit.common import (
    HardwareCheckoutGateway,
    _nested_id,
    _nested_str,
    _parse_date,
    _parse_decimal,
    tenant_for,
)
from core.importers.snipeit.contracts import ImportContext, StageResult


@dataclass(frozen=True)
class HardwareDependencies:
    status_labels: Mapping
    asset_models: Mapping
    tenants: Mapping
    suppliers: Mapping
    locations: Mapping
    custom_fields: Mapping
    holders: Mapping
    assets: MutableMapping
    checkout: HardwareCheckoutGateway
    # value mirrors assets.choices.StatusTypeChoices.DEPLOYED / assets.models.choices.WarrantyTypeChoices.HARDWARE;
    # the composition root (management command) passes the canonical enum values
    deployed_type: str = "deployed"
    warranty_type: str = "hardware"


class HardwareImporter:
    key = "assets"
    endpoint = "/api/v1/hardware"

    def __init__(self, context: ImportContext, dependencies: HardwareDependencies) -> None:
        self.context = context
        self.dependencies = dependencies

    def run(self) -> StageResult:
        Asset = apps.get_model("assets", "Asset")
        StatusLabel = apps.get_model("assets", "StatusLabel")
        result = StageResult(self.key)
        checkout_rows = []
        self.context.reporter.start(result)
        deployed_status = self._deployed_status(StatusLabel)

        for row in self.context.client.get_all(self.endpoint):
            try:
                with transaction.atomic():
                    asset, outcome = self._upsert_asset_and_warranty(Asset, row)
                source_id = row["id"]
                self.dependencies.assets[source_id] = asset
                result.counts.record(outcome)
                checkout_rows.append((row, asset))
            # broad except: task-isolation: one remote row must not abort the reviewed import batch
            except Exception as exc:
                self.context.reporter.row_failure(result, "assets.persist", exc)

        if not self.context.dry_run:
            for row, asset in checkout_rows:
                self._checkout_if_needed(row, asset, deployed_status, result)

        self.context.reporter.finish(result)
        return result

    def _deployed_status(self, StatusLabel):
        if self.context.dry_run:
            return StatusLabel(id=-9999, name="Deployed (imported)", type=self.dependencies.deployed_type)
        status, _ = StatusLabel.objects.get_or_create(
            name="Deployed (imported)",
            defaults={"type": self.dependencies.deployed_type, "color": "007bff"},
        )
        return status

    def _upsert_asset_and_warranty(self, Asset, row: dict):
        Warranty = apps.get_model("assets", "Warranty")
        sid = row["id"]
        asset_tag = (row.get("asset_tag") or "").strip() or f"IMPORT-{sid}"
        serial = (row.get("serial") or "").strip()
        name = (row.get("name") or "").strip() or asset_tag
        asset_type = self.dependencies.asset_models.get(_nested_id(row.get("model")))
        status_obj = self.dependencies.status_labels.get(_nested_id(row.get("status_label")))
        tenant = tenant_for(
            row,
            default_tenant=self.context.default_tenant,
            map_companies=self.context.map_companies,
            tenants=self.dependencies.tenants,
        )
        supplier = self.dependencies.suppliers.get(_nested_id(row.get("supplier")))
        location = self.dependencies.locations.get(_nested_id(row.get("location"))) or self.dependencies.locations.get(
            _nested_id(row.get("rtd_location"))
        )
        purchase_date = _parse_date(_nested_str(row.get("purchase_date"), "date"))
        purchase_cost = _parse_decimal(row.get("purchase_cost"))
        order_number = (row.get("order_number") or "")[:100]
        notes = row.get("notes") or ""
        warranty_expiration = self._warranty_expiration(purchase_date, row.get("warranty_months"))
        cf_data = self._custom_field_data(sid, row.get("custom_fields"))

        obj = Asset.all_objects.filter(custom_field_data__snipeit_id=str(sid)).first()
        if not obj and serial:
            obj = Asset.all_objects.filter(serial_number=serial, tenant=tenant).first()
        if not obj:
            obj = Asset.all_objects.filter(asset_tag=asset_tag, tenant=tenant).first()

        if obj:
            if not self.context.update:
                return obj, "skipped"
            if not self.context.dry_run:
                obj.name = name
                obj.serial_number = serial
                obj.asset_type = asset_type
                obj.status = status_obj
                obj.location = location
                obj.purchase_date = purchase_date
                obj.purchase_cost = purchase_cost
                obj.order_number = order_number
                obj.notes = notes
                obj.supplier = supplier
                obj.custom_field_data.update(cf_data)
                obj.save()
                self._upsert_warranty(Warranty, obj, purchase_date, warranty_expiration, supplier)
            return obj, "updated"

        if self.context.dry_run:
            obj = Asset(id=-sid, asset_tag=asset_tag, tenant=tenant)
        else:
            obj = Asset.objects.create(
                name=name,
                asset_tag=asset_tag,
                serial_number=serial,
                asset_type=asset_type,
                status=status_obj,
                location=location,
                tenant=tenant,
                purchase_date=purchase_date,
                purchase_cost=purchase_cost,
                order_number=order_number,
                notes=notes,
                supplier=supplier,
                custom_field_data=cf_data,
            )
            self._upsert_warranty(Warranty, obj, purchase_date, warranty_expiration, supplier)
        return obj, "created"

    @staticmethod
    def _warranty_expiration(purchase_date, warranty_months):
        if purchase_date and warranty_months:
            try:
                return purchase_date + relativedelta(months=int(warranty_months))
            # broad except: boundary-isolation: malformed warranty months degrade to no expiration
            except (TypeError, ValueError):
                pass
        return None

    def _custom_field_data(self, sid, custom_fields) -> dict:
        data = {"snipeit_id": str(sid)}
        for field_info in (custom_fields or {}).values():
            if not isinstance(field_info, dict):
                continue
            value = field_info.get("value")
            if value is None or value == "":
                continue
            local_field = self.dependencies.custom_fields.get(field_info.get("field") or "")
            if local_field:
                data[local_field.name] = value
        return data

    def _upsert_warranty(self, Warranty, asset, purchase_date, warranty_expiration, supplier) -> None:
        if warranty_expiration and purchase_date and not self.context.dry_run:
            Warranty.objects.update_or_create(
                asset=asset,
                warranty_type=self.dependencies.warranty_type,
                defaults={
                    "start_date": purchase_date,
                    "end_date": warranty_expiration,
                    "provider": supplier.name if supplier else "",
                },
            )

    def _checkout_if_needed(self, row, asset, deployed_status, result: StageResult) -> None:
        assigned_to = row.get("assigned_to")
        if not assigned_to or not asset.pk or asset.pk <= 0:
            return

        try:
            target_type = (assigned_to.get("type") or "").lower()
            target_id = assigned_to.get("id")
            target = None
            kwargs = {}
            if target_type == "user":
                target = self.dependencies.holders.get(target_id)
                kwargs["holder"] = target
            elif target_type == "location":
                target = self.dependencies.locations.get(target_id)
                kwargs["location"] = target
            elif target_type == "asset":
                target = self.dependencies.assets.get(target_id)
                kwargs["asset_target"] = target

            if not target or self._has_exact_assignment(asset, target_type, target):
                return
            self.dependencies.checkout.checkout(
                asset=asset,
                status=deployed_status,
                tenant_id=asset.tenant_id,
                **kwargs,
            )
        # broad except: boundary-isolation: optional child rows may degrade without discarding the parent item
        except Exception as exc:
            self.context.reporter.warning(result, "assets.checkout", exc)

    @staticmethod
    def _has_exact_assignment(asset, target_type: str, target) -> bool:
        AssetAssignment = apps.get_model("assets", "AssetAssignment")
        filters = {
            "asset": asset,
            "is_active": True,
            "deleted_at__isnull": True,
            "assigned_user__isnull": True,
            "assigned_location__isnull": True,
            "assigned_asset__isnull": True,
        }
        field_by_type = {"user": "assigned_user", "location": "assigned_location", "asset": "assigned_asset"}
        target_field = field_by_type.get(target_type)
        if target_field is None:
            return False
        filters.pop(f"{target_field}__isnull")
        filters[target_field] = target
        return AssetAssignment._base_manager.filter(**filters).exists()
