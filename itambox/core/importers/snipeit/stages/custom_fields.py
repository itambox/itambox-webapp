"""Stages for Snipe-IT custom fields and fieldsets."""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass

from django.apps import apps
from django.db import transaction
from django.utils import timezone

from core.importers.snipeit.common import _FIELD_FORMAT_MAP, _clean_field_name
from core.importers.snipeit.contracts import ImportContext, Outcome, StageResult


@dataclass(frozen=True)
class CustomFieldDependencies:
    custom_fields: MutableMapping[str, object]


@dataclass(frozen=True)
class FieldsetDependencies:
    custom_fields: Mapping[str, object]
    fieldsets: MutableMapping[int, object]


def _source_checksum(context, definition_kind, source_id):
    source_url = str(getattr(context.client, "base_url", "")).rstrip("/")
    if not source_url:
        raise ValueError("Cannot establish Snipe-IT source identity")
    tenant = context.default_tenant
    tenant_id = getattr(tenant, "pk", None)
    if tenant_id is None:
        client_context = getattr(context.client, "context", None)
        tenant_id = getattr(client_context, "tenant_id", None)
    target_scope = f"tenant:{tenant_id}" if tenant_id is not None else "source-global"
    material = "\x1f".join(("snipeit", definition_kind, source_url, target_scope, str(source_id)))
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


class CustomFieldImporter:
    key = "fields"

    def __init__(self, context: ImportContext, dependencies: CustomFieldDependencies) -> None:
        self.context = context
        self.dependencies = dependencies

    def _source_checksum(self, definition_kind, source_id):
        return _source_checksum(self.context, definition_kind, source_id)

    @staticmethod
    def _choice_labels(raw_choices):
        return [value.strip() for value in (raw_choices or "").splitlines() if value.strip()]

    def _get_choice_set(self, choice_set_model, source_id, label):
        slug = f"snipeit-{source_id}"
        source_checksum = self._source_checksum("choice-set", source_id)
        choice_set = choice_set_model.all_objects.select_for_update().filter(namespace="local", slug=slug).first()
        if choice_set is not None and choice_set.deleted_at is not None:
            raise ValueError("Snipe-IT Choice Set identity is reserved by a tombstone")
        if choice_set is not None and choice_set.management_kind != "local":
            raise ValueError("Cannot update a managed Choice Set from Snipe-IT")
        if choice_set is not None and choice_set.source_checksum != source_checksum:
            raise ValueError("Cannot claim an unprovenanced Choice Set from Snipe-IT")
        if choice_set is None:
            choice_set = choice_set_model.objects.create(
                namespace="local",
                slug=slug,
                label=f"{label} choices",
                management_kind="local",
                version=1,
                lifecycle="active",
                source_checksum=source_checksum,
            )
        return choice_set

    @staticmethod
    def _refresh_choice_set(choice_set_model, choice_set, label):
        choice_set_model.all_objects.filter(pk=choice_set.pk).update(
            label=f"{label} choices",
            management_kind="local",
            version=1,
            lifecycle="active",
            deleted_at=None,
        )

    @staticmethod
    def _label_match(existing_choices, choice_label, used_existing_ids):
        return next(
            (
                choice
                for choice in existing_choices
                if choice.deleted_at is None and choice.pk not in used_existing_ids and choice.label == choice_label
            ),
            None,
        )

    def _desired_choice_keys(self, existing_choices, choices):
        existing_by_key = {choice.key: choice for choice in existing_choices}
        used_keys = set()
        generated_choices = [(self._choice_key(choice_label, used_keys), choice_label) for choice_label in choices]
        used_existing_ids = set()
        assignments = []
        for generated_key, choice_label in generated_choices:
            existing = existing_by_key.get(generated_key)
            if existing is not None and existing.deleted_at is None and existing.label == choice_label:
                assigned_key = existing.key
                used_existing_ids.add(existing.pk)
            else:
                label_match = self._label_match(existing_choices, choice_label, used_existing_ids)
                assigned_key = label_match.key if label_match is not None else None
                if label_match is not None:
                    used_existing_ids.add(label_match.pk)
            assignments.append((generated_key, choice_label, assigned_key))
        reserved_keys = {assigned_key for _, _, assigned_key in assignments if assigned_key is not None}
        existing_keys = set(existing_by_key)
        generated_keys = {generated_key for generated_key, _, _ in assignments}
        used_output_keys = set()
        desired_choices = []
        for generated_key, choice_label, assigned_key in assignments:
            key = assigned_key
            if key is None:
                key = generated_key
                if key in reserved_keys or key in used_output_keys or key in existing_keys:
                    available_keys = reserved_keys | generated_keys | existing_keys | used_output_keys
                    key = self._choice_key(choice_label, available_keys)
            used_output_keys.add(key)
            desired_choices.append((key or generated_key, choice_label))
        return existing_by_key, desired_choices

    def _reconcile_choice_rows(self, choice_model, choice_set, choices):
        existing_choices = list(
            choice_model.all_objects.select_for_update().filter(choice_set_id=choice_set.pk).order_by("position", "key")
        )
        if any(choice.management_kind != "local" for choice in existing_choices):
            raise ValueError("Cannot update managed Choices from Snipe-IT")
        for choice, temporary_position in zip(
            existing_choices,
            self._temporary_choice_positions(existing_choices),
            strict=True,
        ):
            choice_model.all_objects.filter(pk=choice.pk).update(position=temporary_position)
        existing_by_key, desired_choices = self._desired_choice_keys(existing_choices, choices)
        desired_keys = {key for key, _ in desired_choices}
        if any(
            (choice := existing_by_key.get(key)) is not None and choice.deleted_at is not None
            for key, _ in desired_choices
        ):
            raise ValueError("Snipe-IT Choice identity is reserved by a tombstone")
        choice_model.all_objects.filter(choice_set_id=choice_set.pk, deleted_at__isnull=True).exclude(
            key__in=desired_keys
        ).update(lifecycle="deprecated", deleted_at=timezone.now())
        for position, (key, choice_label) in enumerate(desired_choices, start=1):
            choice = existing_by_key.get(key)
            if choice is None:
                choice_model.objects.create(
                    choice_set=choice_set,
                    key=key,
                    label=choice_label,
                    position=position * 10,
                    management_kind="local",
                    version=1,
                    lifecycle="active",
                )
                continue
            choice_model.all_objects.filter(pk=choice.pk).update(
                label=choice_label,
                position=position * 10,
                management_kind="local",
                version=1,
                lifecycle="active",
                deleted_at=None,
            )

    def _choice_set(self, choice_set_model, choice_model, source_id, label, raw_choices):
        choices = self._choice_labels(raw_choices)
        choice_set = self._get_choice_set(choice_set_model, source_id, label)
        self._refresh_choice_set(choice_set_model, choice_set, label)
        self._reconcile_choice_rows(choice_model, choice_set, choices)
        return choice_set

    @staticmethod
    def _temporary_choice_positions(existing_choices):
        if not existing_choices:
            return []
        occupied = {choice.position for choice in existing_choices}
        positions = []
        for candidate in range(900000, 1000001):
            if candidate not in occupied:
                positions.append(candidate)
                if len(positions) == len(existing_choices):
                    return positions
        raise ValueError("Too many choices for safe reconciliation")

    @staticmethod
    def _choice_key(label, used=None):
        used = set() if used is None else used
        base = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii").casefold()
        base = "".join(char if char in "abcdefghijklmnopqrstuvwxyz0123456789_" else "_" for char in base).strip("_")
        candidate = base[:63] or "choice"
        if candidate in used:
            digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:8]
            candidate = f"{(base[:54] or 'choice').rstrip('_')}_{digest}"
            ordinal = 0
            while candidate in used:
                ordinal += 1
                digest = hashlib.sha256(f"{label}\\x1f{ordinal}".encode("utf-8")).hexdigest()[:8]
                candidate = f"{(base[:54] or 'choice').rstrip('_')}_{digest}"
        used.add(candidate)
        return candidate

    @staticmethod
    def _field_name(db_column, source_id):
        raw_name = _clean_field_name(db_column) if db_column else f"snipeit_field_{source_id}"
        if len(raw_name) <= 64:
            return raw_name
        digest = hashlib.sha256((db_column or str(source_id)).encode("utf-8")).hexdigest()[:8]
        return f"{raw_name[:55].rstrip('_')}_{digest}"

    def _apply_choice_set_defaults(
        self, defaults, obj, row, field_type, choice_set_model, choice_model, source_id, label, raw_choices
    ):
        if field_type != "single-select":
            return
        defaults["max_values"] = 1
        if obj is not None and "field_values" not in row:
            defaults["choice_set"] = obj.choice_set
        elif self.context.dry_run:
            defaults["choice_set"] = choice_set_model(
                namespace="local",
                slug=f"snipeit-{source_id}",
                label=f"{label} choices",
                management_kind="local",
                version=1,
                lifecycle="active",
                source_checksum=self._source_checksum("choice-set", source_id),
            )
        else:
            defaults["choice_set"] = self._choice_set(
                choice_set_model,
                choice_model,
                source_id,
                label,
                raw_choices,
            )

    def _upsert(self, model, asset_ct, choice_set_model, choice_model, row) -> tuple[object, Outcome]:
        source_id = row["id"]
        db_column = row.get("db_column_name") or ""
        raw_name = self._field_name(db_column, source_id)
        label = (row.get("name") or raw_name).strip()[:200]
        field_type = _FIELD_FORMAT_MAP.get((row.get("format") or row.get("type") or "TEXT").upper(), "text")
        raw_choices = row.get("field_values") or ""
        defaults = {
            "namespace": "local",
            "label": label,
            "help_text": "Imported from Snipe-IT",
            "field_type": field_type,
            "scope": "asset",
            "management_kind": "local",
            "version": 1,
            "lifecycle": "active",
            "required": False,
            "nullable": False,
            "mappings": [],
            "source_checksum": self._source_checksum("custom-field", source_id),
            "decimal_scale": 2 if field_type == "decimal" else None,
            "choice_set": None,
        }
        obj = model.all_objects.select_for_update().filter(name=raw_name).first()
        if obj and obj.deleted_at is not None:
            raise ValueError("Snipe-IT Custom Field identity is reserved by a tombstone")
        if obj and obj.management_kind != "local":
            raise ValueError("Cannot update a managed Custom Field from Snipe-IT")
        if obj and obj.source_checksum != defaults["source_checksum"]:
            raise ValueError("Cannot claim an unprovenanced Custom Field from Snipe-IT")
        if obj and not self.context.update:
            return obj, "skipped"
        self._apply_choice_set_defaults(
            defaults,
            obj,
            row,
            field_type,
            choice_set_model,
            choice_model,
            source_id,
            label,
            raw_choices,
        )
        candidate = obj or model()
        for field, value in defaults.items():
            setattr(candidate, field, value)
        candidate.validate_definition_contract(object_types=[asset_ct])
        if obj:
            if not self.context.dry_run:
                for field, value in defaults.items():
                    setattr(obj, field, value)
                obj.save()
                obj.object_types.set([asset_ct])
            return obj, "updated"
        if not self.context.dry_run:
            obj = model.objects.create(name=raw_name, **defaults)
            obj.object_types.add(asset_ct)
        else:
            obj = model(id=-source_id, name=raw_name, **defaults)
        return obj, "created"

    def run(self) -> StageResult:
        model = apps.get_model("extras", "CustomField")
        choice_set_model = apps.get_model("extras", "CustomFieldChoiceSet")
        choice_model = apps.get_model("extras", "CustomFieldChoice")
        asset_model = apps.get_model("assets", "Asset")
        content_type_model = apps.get_model("contenttypes", "ContentType")
        asset_ct = content_type_model.objects.get_for_model(asset_model)
        result = StageResult(self.key)
        self.context.reporter.start(result)
        for row in self.context.client.get_all("/api/v1/fields"):
            try:
                with transaction.atomic():
                    obj, outcome = self._upsert(model, asset_ct, choice_set_model, choice_model, row)
                db_column = row.get("db_column_name") or ""
                self.dependencies.custom_fields[db_column] = obj
                result.counts.record(outcome)
            # broad except: task-isolation: one remote row must not abort the reviewed import batch
            except Exception as exc:
                self.context.reporter.row_failure(result, "fields.persist", exc)
        self.context.reporter.finish(result)
        return result


class FieldsetImporter:
    key = "fieldsets"

    def __init__(self, context: ImportContext, dependencies: FieldsetDependencies) -> None:
        self.context = context
        self.dependencies = dependencies

    def _source_checksum(self, source_id):
        return _source_checksum(self.context, "fieldset", source_id)

    def _reconcile_memberships(self, membership_model, fieldset, row):
        field_payload = row.get("fields")
        if not isinstance(field_payload, Mapping) or "rows" not in field_payload:
            return
        remote_field_rows = field_payload.get("rows") or []
        field_objects = [
            self.dependencies.custom_fields[db_column]
            for row2 in remote_field_rows
            if (db_column := row2.get("db_column_name")) and db_column in self.dependencies.custom_fields
        ]
        if remote_field_rows and len(field_objects) != len(remote_field_rows):
            return
        membership_model.objects.filter(fieldset_id=fieldset.pk).delete()
        if field_objects:
            membership_model.objects.bulk_create(
                [
                    membership_model(fieldset_id=fieldset.pk, custom_field_id=field.pk, position=index * 10)
                    for index, field in enumerate(field_objects, start=1)
                ]
            )

    def _upsert(self, model, membership_model, row) -> tuple[object, Outcome]:
        source_id = row["id"]
        label = (row.get("name") or "").strip() or f"Fieldset {source_id}"
        slug = f"snipeit-{source_id}"
        defaults = {
            "namespace": "local",
            "slug": slug,
            "label": label[:200],
            "description": "Imported from Snipe-IT",
            "management_kind": "local",
            "version": 1,
            "lifecycle": "active",
            "source_checksum": self._source_checksum(source_id),
        }
        obj = model.all_objects.select_for_update().filter(namespace="local", slug=slug).first()
        created = False
        if obj and obj.deleted_at is not None:
            raise ValueError("Snipe-IT Fieldset identity is reserved by a tombstone")
        if obj and obj.management_kind != "local":
            raise ValueError("Cannot update a managed Fieldset from Snipe-IT")
        if obj and obj.source_checksum != defaults["source_checksum"]:
            raise ValueError("Cannot claim an unprovenanced Fieldset from Snipe-IT")
        if obj:
            if not self.context.update:
                return obj, "skipped"
            if not self.context.dry_run:
                model.all_objects.filter(pk=obj.pk).update(**defaults)
        elif not self.context.dry_run:
            obj = model.objects.create(**defaults)
            created = True
        else:
            obj = model(id=-source_id, **defaults)
            created = True
        if not self.context.dry_run:
            self._reconcile_memberships(membership_model, obj, row)
        return obj, "created" if created else "updated"

    def run(self) -> StageResult:
        model = apps.get_model("extras", "CustomFieldset")
        membership_model = apps.get_model("extras", "CustomFieldsetField")
        result = StageResult(self.key)
        self.context.reporter.start(result)
        for row in self.context.client.get_all("/api/v1/fieldsets"):
            source_id = row["id"]
            try:
                with transaction.atomic():
                    obj, outcome = self._upsert(model, membership_model, row)
                self.dependencies.fieldsets[source_id] = obj
                result.counts.record(outcome)
            # broad except: task-isolation: one remote row must not abort the reviewed import batch
            except Exception as exc:
                self.context.reporter.row_failure(result, "fieldsets.persist", exc)
        self.context.reporter.finish(result)
        return result
