"""Stages for Snipe-IT catalog resources."""

from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass

from django.apps import apps
from django.db import transaction

from core.importers.snipeit.common import _CATEGORY_APPLIES_MAP, _STATUS_TYPE_MAP
from core.importers.snipeit.contracts import ImportContext, Outcome, StageResult


@dataclass(frozen=True)
class StatusLabelDependencies:
    status_labels: MutableMapping[int, object]


@dataclass(frozen=True)
class ManufacturerDependencies:
    manufacturers: MutableMapping[int, object]


@dataclass(frozen=True)
class CategoryDependencies:
    categories: MutableMapping[int, object]


@dataclass(frozen=True)
class SupplierDependencies:
    suppliers: MutableMapping[int, object]


class StatusLabelImporter:
    key = "statuslabels"

    def __init__(self, context: ImportContext, dependencies: StatusLabelDependencies) -> None:
        self.context = context
        self.dependencies = dependencies

    def _upsert(self, model, row) -> tuple[object, Outcome]:
        source_id = row["id"]
        name = row.get("name", "").strip() or f"Imported Status {source_id}"
        snipe_type = (row.get("type") or "").lower()
        itam_type = _STATUS_TYPE_MAP.get(snipe_type, "deployable")
        obj = model.all_objects.filter(name=name).first()
        if obj and not self.context.update:
            return obj, "skipped"
        if obj and self.context.update:
            if not self.context.dry_run:
                obj.type = itam_type
                obj.save(update_fields=["type"])
            return obj, "updated"
        if not self.context.dry_run:
            obj = model.objects.create(name=name, type=itam_type, color="6c757d")
        else:
            obj = model(id=-source_id, name=name, type=itam_type)
        return obj, "created"

    def run(self) -> StageResult:
        model = apps.get_model("assets", "StatusLabel")
        result = StageResult(self.key)
        self.context.reporter.start(result)
        for row in self.context.client.get_all("/api/v1/statuslabels"):
            source_id = row["id"]
            try:
                with transaction.atomic():
                    obj, outcome = self._upsert(model, row)
                self.dependencies.status_labels[source_id] = obj
                result.counts.record(outcome)
            except Exception as exc:
                self.context.reporter.row_failure(result, "statuslabels.persist", exc)
        self.context.reporter.finish(result)
        return result


class ManufacturerImporter:
    key = "manufacturers"

    def __init__(self, context: ImportContext, dependencies: ManufacturerDependencies) -> None:
        self.context = context
        self.dependencies = dependencies

    def _upsert(self, model, row) -> tuple[object, Outcome]:
        source_id = row["id"]
        name = (row.get("name") or "").strip() or f"Manufacturer {source_id}"
        obj = model.all_objects.filter(name=name).first()
        if obj and not self.context.update:
            return obj, "skipped"
        if obj and self.context.update:
            return obj, "updated"
        if not self.context.dry_run:
            obj, created = model.objects.get_or_create(name=name)
        else:
            obj = model(id=-source_id, name=name)
            created = True
        return obj, "created" if created else "skipped"

    def run(self) -> StageResult:
        model = apps.get_model("assets", "Manufacturer")
        result = StageResult(self.key)
        self.context.reporter.start(result)
        for row in self.context.client.get_all("/api/v1/manufacturers"):
            source_id = row["id"]
            try:
                with transaction.atomic():
                    obj, outcome = self._upsert(model, row)
                self.dependencies.manufacturers[source_id] = obj
                result.counts.record(outcome)
            except Exception as exc:
                self.context.reporter.row_failure(result, "manufacturers.persist", exc)
        self.context.reporter.finish(result)
        return result


class CategoryImporter:
    key = "categories"

    def __init__(self, context: ImportContext, dependencies: CategoryDependencies) -> None:
        self.context = context
        self.dependencies = dependencies

    def _upsert(self, model, row) -> tuple[object, Outcome]:
        source_id = row["id"]
        name = (row.get("name") or "").strip() or f"Category {source_id}"
        cat_type = (row.get("category_type") or "asset").lower()
        applies_to = _CATEGORY_APPLIES_MAP.get(cat_type, {"asset": True})
        obj = model.all_objects.filter(name=name).first()
        if obj and not self.context.update:
            return obj, "skipped"
        if obj and self.context.update:
            if not self.context.dry_run:
                obj.applies_to = applies_to
                obj.save(update_fields=["applies_to"])
            return obj, "updated"
        if not self.context.dry_run:
            obj = model.objects.create(name=name, applies_to=applies_to)
        else:
            obj = model(id=-source_id, name=name, applies_to=applies_to)
        return obj, "created"

    def run(self) -> StageResult:
        model = apps.get_model("assets", "Category")
        result = StageResult(self.key)
        self.context.reporter.start(result)
        for row in self.context.client.get_all("/api/v1/categories"):
            source_id = row["id"]
            try:
                with transaction.atomic():
                    obj, outcome = self._upsert(model, row)
                self.dependencies.categories[source_id] = obj
                result.counts.record(outcome)
            except Exception as exc:
                self.context.reporter.row_failure(result, "categories.persist", exc)
        self.context.reporter.finish(result)
        return result


class SupplierImporter:
    key = "suppliers"

    def __init__(self, context: ImportContext, dependencies: SupplierDependencies) -> None:
        self.context = context
        self.dependencies = dependencies

    def _upsert(self, supplier_model, role_model, contact_model, assignment_model, ct_model, row):
        source_id = row["id"]
        name = (row.get("name") or "").strip() or f"Supplier {source_id}"
        contact_email = (row.get("email") or "")[:254]
        contact_phone = (row.get("phone") or "")[:50]
        contact_name = (row.get("contact") or "")[:255]
        defaults = {
            "website": (row.get("url") or "")[:200],
            "notes": row.get("notes") or "",
            "custom_field_data": {"snipeit_id": str(source_id)},
        }
        obj = supplier_model.all_objects.filter(custom_field_data__snipeit_id=str(source_id)).first()
        if not obj:
            obj = supplier_model.all_objects.filter(name=name).first()
        if obj:
            if not self.context.update:
                return obj, "skipped"
            if not self.context.dry_run:
                for field, value in defaults.items():
                    setattr(obj, field, value)
                obj.save()
            return obj, "updated"
        if not self.context.dry_run:
            obj = supplier_model.objects.create(name=name, **defaults)
            if contact_name or contact_email or contact_phone:
                supplier_ct = ct_model.objects.get_for_model(supplier_model)
                primary_role, _ = role_model.objects.get_or_create(
                    slug="primary-contact",
                    defaults={"name": "Primary Contact", "description": "Primary Contact"},
                )
                contact = contact_model.objects.create(
                    name=contact_name or f"{name} Contact",
                    phone=contact_phone,
                    email=contact_email,
                )
                assignment_model.objects.create(
                    contact=contact,
                    role=primary_role,
                    content_type=supplier_ct,
                    object_id=obj.pk,
                    priority="primary",
                )
        else:
            obj = supplier_model(id=-source_id, name=name)
        return obj, "created"

    def run(self) -> StageResult:
        supplier_model = apps.get_model("assets", "Supplier")
        role_model = apps.get_model("organization", "ContactRole")
        contact_model = apps.get_model("organization", "Contact")
        assignment_model = apps.get_model("organization", "ContactAssignment")
        ct_model = apps.get_model("contenttypes", "ContentType")
        result = StageResult(self.key)
        self.context.reporter.start(result)
        for row in self.context.client.get_all("/api/v1/suppliers"):
            source_id = row["id"]
            try:
                with transaction.atomic():
                    obj, outcome = self._upsert(
                        supplier_model, role_model, contact_model, assignment_model, ct_model, row
                    )
                self.dependencies.suppliers[source_id] = obj
                result.counts.record(outcome)
            except Exception as exc:
                self.context.reporter.row_failure(result, "suppliers.persist", exc)
        self.context.reporter.finish(result)
        return result
