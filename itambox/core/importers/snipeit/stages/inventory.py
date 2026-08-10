from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from django.apps import apps
from django.db import transaction

from core.importers.snipeit.common import InventoryAssignmentGateway, _nested_id, tenant_for
from core.importers.snipeit.contracts import ImportContext, StageResult


@dataclass(frozen=True)
class AccessoryDependencies:
    manufacturers: Mapping
    categories: Mapping
    suppliers: Mapping
    tenants: Mapping
    holders: Mapping
    assignments: InventoryAssignmentGateway


@dataclass(frozen=True)
class ConsumableDependencies:
    manufacturers: Mapping
    categories: Mapping
    suppliers: Mapping
    tenants: Mapping


@dataclass(frozen=True)
class ComponentDependencies:
    manufacturers: Mapping
    categories: Mapping
    suppliers: Mapping
    tenants: Mapping
    assets: Mapping
    assignments: InventoryAssignmentGateway


class AccessoryImporter:
    key = "accessories"

    def __init__(self, context: ImportContext, dependencies: AccessoryDependencies) -> None:
        self.context = context
        self.dependencies = dependencies

    def run(self) -> StageResult:
        Accessory = apps.get_model("inventory", "Accessory")
        AccessoryStock = apps.get_model("inventory", "AccessoryStock")
        AccessoryAssignment = apps.get_model("inventory", "AccessoryAssignment")
        Location = apps.get_model("organization", "Location")
        result = StageResult(self.key)
        self.context.reporter.start(result)

        for row in self.context.client.get_all("/api/v1/accessories"):
            try:
                self._process_row(row, result, Accessory, AccessoryStock, Location, AccessoryAssignment)
            except Exception as exc:
                self.context.reporter.row_failure(result, "accessories.persist", exc)

        self.context.reporter.finish(result)
        return result

    def _process_row(self, row, result, Accessory, AccessoryStock, Location, AccessoryAssignment) -> None:
        sid = row["id"]
        name = (row.get("name") or "").strip() or f"Accessory {sid}"
        mfr = self.dependencies.manufacturers.get(_nested_id(row.get("manufacturer")))
        cat = self.dependencies.categories.get(_nested_id(row.get("category")))
        supplier = self.dependencies.suppliers.get(_nested_id(row.get("supplier")))
        tenant = tenant_for(
            row,
            default_tenant=self.context.default_tenant,
            map_companies=self.context.map_companies,
            tenants=self.dependencies.tenants,
        )
        qty = row.get("qty") or 1
        defaults = {
            "manufacturer": mfr,
            "category": cat,
            "supplier": supplier,
            "tenant": tenant,
            "notes": row.get("notes") or "",
            "custom_field_data": {"snipeit_id": str(sid)},
        }
        with transaction.atomic():
            obj = Accessory.all_objects.filter(custom_field_data__snipeit_id=str(sid)).first()
            if not obj:
                obj = Accessory.all_objects.filter(name=name, tenant=tenant).first()
            if obj:
                if not self.context.update:
                    result.counts.skipped += 1
                else:
                    if not self.context.dry_run:
                        for field, value in defaults.items():
                            setattr(obj, field, value)
                        obj.save()
                    result.counts.updated += 1
                if not self.context.dry_run:
                    self._import_checkouts(obj, sid, AccessoryAssignment, result)
                return

            if not self.context.dry_run:
                obj = Accessory.objects.create(name=name, **defaults)
                loc = Location.objects.filter(tenant=tenant).first() if tenant else None
                if loc:
                    AccessoryStock.objects.create(accessory=obj, location=loc, qty=qty)
                self._import_checkouts(obj, sid, AccessoryAssignment, result)
            else:
                obj = Accessory(id=-sid, name=name, tenant=tenant)
            result.counts.created += 1

    def _import_checkouts(self, accessory, snipe_id: int, assignment_model, result: StageResult) -> None:
        try:
            for checkout in self.context.client.get_all(f"/api/v1/accessories/{snipe_id}/checkedout"):
                user_id = _nested_id(checkout.get("assigned_to"))
                if not user_id:
                    continue
                holder = self.dependencies.holders.get(user_id)
                if not holder or not holder.pk or holder.pk <= 0:
                    continue
                qty = checkout.get("qty") or 1
                if assignment_model._base_manager.filter(
                    accessory=accessory,
                    assigned_holder=holder,
                    deleted_at__isnull=True,
                ).exists():
                    continue
                self.dependencies.assignments.assign(accessory, qty, holder=holder)
        except Exception as exc:
            self.context.reporter.warning(result, "accessories.checkouts", exc)


class ConsumableImporter:
    key = "consumables"

    def __init__(self, context: ImportContext, dependencies: ConsumableDependencies) -> None:
        self.context = context
        self.dependencies = dependencies

    def run(self) -> StageResult:
        Consumable = apps.get_model("inventory", "Consumable")
        ConsumableStock = apps.get_model("inventory", "ConsumableStock")
        Location = apps.get_model("organization", "Location")
        result = StageResult(self.key)
        self.context.reporter.start(result)

        for row in self.context.client.get_all("/api/v1/consumables"):
            try:
                self._process_row(row, result, Consumable, ConsumableStock, Location)
            except Exception as exc:
                self.context.reporter.row_failure(result, "consumables.persist", exc)

        self.context.reporter.finish(result)
        return result

    def _process_row(self, row, result, Consumable, ConsumableStock, Location) -> None:
        sid = row["id"]
        name = (row.get("name") or "").strip() or f"Consumable {sid}"
        mfr = self.dependencies.manufacturers.get(_nested_id(row.get("manufacturer")))
        cat = self.dependencies.categories.get(_nested_id(row.get("category")))
        supplier = self.dependencies.suppliers.get(_nested_id(row.get("supplier")))
        tenant = tenant_for(
            row,
            default_tenant=self.context.default_tenant,
            map_companies=self.context.map_companies,
            tenants=self.dependencies.tenants,
        )
        qty = row.get("qty") or 0
        defaults = {
            "manufacturer": mfr,
            "category": cat,
            "supplier": supplier,
            "tenant": tenant,
            "notes": row.get("notes") or "",
            "custom_field_data": {"snipeit_id": str(sid)},
        }
        with transaction.atomic():
            obj = Consumable.all_objects.filter(custom_field_data__snipeit_id=str(sid)).first()
            if not obj:
                obj = Consumable.all_objects.filter(name=name, tenant=tenant).first()
            if obj:
                if not self.context.update:
                    result.counts.skipped += 1
                else:
                    if not self.context.dry_run:
                        for field, value in defaults.items():
                            setattr(obj, field, value)
                        obj.save()
                    result.counts.updated += 1
                return

            if not self.context.dry_run:
                obj = Consumable.objects.create(name=name, **defaults)
                loc = Location.objects.filter(tenant=tenant).first() if tenant else None
                if loc and qty:
                    ConsumableStock.objects.create(consumable=obj, location=loc, qty=qty)
            else:
                obj = Consumable(id=-sid, name=name, tenant=tenant)
            result.counts.created += 1


class ComponentImporter:
    key = "components"

    def __init__(self, context: ImportContext, dependencies: ComponentDependencies) -> None:
        self.context = context
        self.dependencies = dependencies

    def run(self) -> StageResult:
        Component = apps.get_model("inventory", "Component")
        ComponentStock = apps.get_model("inventory", "ComponentStock")
        ComponentAllocation = apps.get_model("inventory", "ComponentAllocation")
        Location = apps.get_model("organization", "Location")
        result = StageResult(self.key)
        self.context.reporter.start(result)

        for row in self.context.client.get_all("/api/v1/components"):
            try:
                self._process_row(row, result, Component, ComponentStock, ComponentAllocation, Location)
            except Exception as exc:
                self.context.reporter.row_failure(result, "components.persist", exc)

        self.context.reporter.finish(result)
        return result

    def _process_row(self, row, result, Component, ComponentStock, ComponentAllocation, Location) -> None:
        sid = row["id"]
        name = (row.get("name") or "").strip() or f"Component {sid}"
        mfr = self.dependencies.manufacturers.get(_nested_id(row.get("manufacturer")))
        cat = self.dependencies.categories.get(_nested_id(row.get("category")))
        supplier = self.dependencies.suppliers.get(_nested_id(row.get("supplier")))
        tenant = tenant_for(
            row,
            default_tenant=self.context.default_tenant,
            map_companies=self.context.map_companies,
            tenants=self.dependencies.tenants,
        )
        qty = row.get("qty") or 0
        defaults = {
            "manufacturer": mfr,
            "category": cat,
            "supplier": supplier,
            "tenant": tenant,
            "notes": row.get("notes") or "",
            "custom_field_data": {"snipeit_id": str(sid)},
        }
        with transaction.atomic():
            obj = Component.all_objects.filter(custom_field_data__snipeit_id=str(sid)).first()
            if not obj:
                obj = Component.all_objects.filter(name=name, manufacturer=mfr).first()
            if obj:
                if not self.context.update:
                    result.counts.skipped += 1
                else:
                    if not self.context.dry_run:
                        for field, value in defaults.items():
                            setattr(obj, field, value)
                        obj.save()
                    result.counts.updated += 1
                if not self.context.dry_run:
                    self._import_allocations(obj, sid, ComponentAllocation, result)
                return

            if not self.context.dry_run:
                obj = Component.objects.create(name=name, **defaults)
                loc = Location.objects.filter(tenant=tenant).first() if tenant else None
                if loc and qty:
                    ComponentStock.objects.create(component=obj, location=loc, qty=qty)
                self._import_allocations(obj, sid, ComponentAllocation, result)
            else:
                obj = Component(id=-sid, name=name, tenant=tenant)
            result.counts.created += 1

    def _import_allocations(self, component, snipe_id: int, allocation_model, result: StageResult) -> None:
        try:
            for allocation in self.context.client.get_all(f"/api/v1/components/{snipe_id}/assets"):
                asset_id = allocation.get("id")
                if not asset_id:
                    continue
                asset = self.dependencies.assets.get(asset_id)
                if not asset or not asset.pk or asset.pk <= 0:
                    continue
                qty = allocation.get("qty") or 1
                if allocation_model._base_manager.filter(
                    component=component,
                    assigned_asset=asset,
                    deleted_at__isnull=True,
                ).exists():
                    continue
                self.dependencies.assignments.assign(component, qty, asset=asset)
        except Exception as exc:
            self.context.reporter.warning(result, "components.allocations", exc)
