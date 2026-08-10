from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from django.apps import apps
from django.db import transaction

from core.importers.snipeit.common import IMPORT_NOTE, _nested_id, _nested_str, _parse_date, _parse_decimal, tenant_for
from core.importers.snipeit.contracts import ImportContext, StageResult


@dataclass(frozen=True)
class LicenseDependencies:
    manufacturers: Mapping
    suppliers: Mapping
    tenants: Mapping
    holders: Mapping
    assets: Mapping


class LicenseImporter:
    key = "licenses"

    def __init__(self, context: ImportContext, dependencies: LicenseDependencies) -> None:
        self.context = context
        self.dependencies = dependencies
        self._software_cache = {}

    def run(self) -> StageResult:
        License = apps.get_model("licenses", "License")
        Software = apps.get_model("software", "Software")
        LicenseSeatAssignment = apps.get_model("licenses", "LicenseSeatAssignment")
        result = StageResult(self.key)
        self.context.reporter.start(result)

        for row in self.context.client.get_all("/api/v1/licenses"):
            try:
                self._process_row(row, result, License, Software, LicenseSeatAssignment)
            except Exception as exc:
                self.context.reporter.row_failure(result, "licenses.persist", exc)

        self.context.reporter.finish(result)
        return result

    def _process_row(self, row, result, License, Software, LicenseSeatAssignment) -> None:
        sid = row["id"]
        name = (row.get("name") or "").strip() or f"License {sid}"
        mfr = self.dependencies.manufacturers.get(_nested_id(row.get("manufacturer")))
        sw_name = (row.get("product_name") or name).strip()
        tenant = tenant_for(
            row,
            default_tenant=self.context.default_tenant,
            map_companies=self.context.map_companies,
            tenants=self.dependencies.tenants,
        )
        supplier = self.dependencies.suppliers.get(_nested_id(row.get("supplier")))
        seats = row.get("seats") or 1
        product_key = row.get("serial") or ""
        purchase_date = _parse_date(_nested_str(row.get("purchase_date"), "date"))
        expiration_date = _parse_date(_nested_str(row.get("expiration_date"), "date"))
        purchase_cost = _parse_decimal(row.get("purchase_cost"))
        order_number = (row.get("order_number") or "")[:100]
        notes = row.get("notes") or ""
        license_type = "subscription_seat" if expiration_date else "perpetual_seat"

        with transaction.atomic():
            software = self._software_for(sid, sw_name, mfr, Software)
            license_obj = License.all_objects.filter(custom_field_data__snipeit_id=str(sid)).first()
            if not license_obj:
                license_obj = License.all_objects.filter(name=name, software=software, tenant=tenant).first()
            if license_obj:
                self._existing_license(
                    license_obj,
                    sid,
                    result,
                    LicenseSeatAssignment,
                    seats=seats,
                    product_key=product_key,
                    purchase_date=purchase_date,
                    expiration_date=expiration_date,
                    purchase_cost=purchase_cost,
                    order_number=order_number,
                    notes=notes,
                    license_type=license_type,
                )
                return

            if not self.context.dry_run:
                license_obj = License.objects.create(
                    name=name,
                    software=software,
                    license_type=license_type,
                    product_key=product_key,
                    seats=seats,
                    purchase_date=purchase_date,
                    expiration_date=expiration_date,
                    purchase_cost=purchase_cost,
                    order_number=order_number,
                    notes=notes,
                    supplier=supplier,
                    tenant=tenant,
                    custom_field_data={"snipeit_id": str(sid)},
                )
                self._import_seats(license_obj, sid, LicenseSeatAssignment, result)
            else:
                license_obj = License(id=-sid, name=name, software=software, tenant=tenant)
            result.counts.created += 1

    def _existing_license(
        self,
        license_obj,
        sid: int,
        result: StageResult,
        assignment_model,
        *,
        seats,
        product_key,
        purchase_date,
        expiration_date,
        purchase_cost,
        order_number,
        notes,
        license_type,
    ) -> None:
        if not self.context.update:
            result.counts.skipped += 1
        else:
            if not self.context.dry_run:
                license_obj.seats = seats
                license_obj.product_key = product_key
                license_obj.purchase_date = purchase_date
                license_obj.expiration_date = expiration_date
                license_obj.purchase_cost = purchase_cost
                license_obj.order_number = order_number
                license_obj.notes = notes
                license_obj.license_type = license_type
                license_obj.custom_field_data = {
                    **(license_obj.custom_field_data or {}),
                    "snipeit_id": str(sid),
                }
                license_obj.save()
            result.counts.updated += 1
        if not self.context.dry_run:
            self._import_seats(license_obj, sid, assignment_model, result)

    def _software_for(self, sid: int, sw_name: str, mfr, Software):
        if sid not in self._software_cache:
            if not self.context.dry_run:
                software_query = Software.all_objects.filter(name=sw_name, manufacturer=mfr)
                if not mfr:
                    software_query = Software.all_objects.filter(name=sw_name)
                software = software_query.first()
                if not software:
                    software = Software.objects.create(
                        name=sw_name,
                        manufacturer=mfr,
                        custom_field_data={"snipeit_id": f"sw_{sid}"},
                    )
            else:
                software = Software(id=-sid, name=sw_name)
            self._software_cache[sid] = software
        return self._software_cache[sid]

    def _import_seats(self, license_obj, snipe_id: int, assignment_model, result: StageResult) -> None:
        try:
            for seat in self.context.client.get_all(f"/api/v1/licenses/{snipe_id}/seats"):
                assigned_user = seat.get("assigned_user") or {}
                assigned_asset = seat.get("assigned_asset") or {}
                holder_id = assigned_user.get("id")
                asset_id = assigned_asset.get("id")
                holder = self.dependencies.holders.get(holder_id) if holder_id else None
                asset = self.dependencies.assets.get(asset_id) if asset_id else None
                if not holder and not asset:
                    continue
                if holder and holder.pk and holder.pk > 0:
                    assignment_model.objects.get_or_create(
                        license=license_obj,
                        assigned_holder=holder,
                        defaults={"notes": IMPORT_NOTE},
                    )
                elif asset and asset.pk and asset.pk > 0:
                    assignment_model.objects.get_or_create(
                        license=license_obj,
                        asset=asset,
                        defaults={"notes": IMPORT_NOTE},
                    )
        except Exception as exc:
            self.context.reporter.warning(result, "licenses.seats", exc)
