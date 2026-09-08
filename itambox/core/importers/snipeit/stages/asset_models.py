"""Stage for Snipe-IT asset models."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass

from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from assets.services.specifications._command_support import (
    load_prospective_definition,
    resource_revision_for_owner,
    stored_values_for,
)
from assets.services.specifications.commands import set_asset_type_composition
from assets.services.specifications.contracts import (
    CommandRejectedDTO,
    DefinitionRevision,
    ExplicitFieldsetSelectionDTO,
    SpecificationPatchDTO,
)
from core.importers.snipeit.common import _nested_id
from core.importers.snipeit.contracts import ImportContext, Outcome, StageResult
from organization.services.access_scope import ActorContextDTO, authentication_revision_for_actor

_OMITTED_FIELDSET = object()


@dataclass(frozen=True)
class AssetModelDependencies:
    manufacturers: Mapping[int, object]
    categories: Mapping[int, object]
    fieldsets: Mapping[int, object]
    asset_models: MutableMapping[int, object]


def _snipeit_identity(context, source_id):
    source_url = str(getattr(context.client, "base_url", "")).rstrip("/")
    if not source_url:
        raise ValueError("Cannot establish Snipe-IT source identity")
    return {"source_url": source_url, "source_id": str(source_id)}


def _connector_identity(source_identity):
    material = json.dumps(
        {
            "source_url": source_identity["source_url"],
            "source_id": source_identity["source_id"],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _actor_context_for_user(user: object) -> ActorContextDTO:
    if not getattr(user, "is_authenticated", False) or not getattr(user, "pk", None):
        raise PermissionDenied("An authenticated actor is required for specification changes.")
    return ActorContextDTO(
        actor_id=int(user.pk),
        authentication_revision=authentication_revision_for_actor(user),
    )


def _require_command_success(result: object) -> object:
    if isinstance(result, CommandRejectedDTO):
        messages = [issue.message_key for issue in result.issues]
        raise ValidationError("; ".join(messages) or "The specification command was rejected.")
    return result


class AssetModelImporter:
    key = "models"

    def __init__(self, context: ImportContext, dependencies: AssetModelDependencies) -> None:
        self.context = context
        self.dependencies = dependencies

    @staticmethod
    def _find_existing(model, connector_identity, model_name, manufacturer):
        locked = model.all_objects.select_for_update()
        source_matches = list(locked.filter(connector_identity=connector_identity)[:2])
        if len(source_matches) > 1:
            raise ValueError("Ambiguous Snipe-IT source ID")
        if source_matches:
            return source_matches[0]
        attribute_matches = list(locked.filter(model=model_name, manufacturer=manufacturer)[:2])
        if any(match.management_kind != "local" for match in attribute_matches):
            raise ValueError("Cannot modify a managed Asset Type from Snipe-IT")
        if len(attribute_matches) > 1:
            raise ValueError("Ambiguous Asset Type attributes")
        if not attribute_matches:
            return None
        existing = attribute_matches[0]
        existing_data = existing.custom_field_data or {}
        if not isinstance(existing_data, Mapping):
            raise ValueError("Existing Asset Type specifications are not a JSON object")
        existing_identity = existing.connector_identity
        if existing_identity not in (None, "") and existing_identity != connector_identity:
            raise ValueError("Asset Type attributes belong to another Snipe-IT identity")
        if existing_data.get("snipeit_id") not in (None, ""):
            raise ValueError("Asset Type has legacy unprovenanced Snipe-IT identity")
        raise ValueError("Cannot claim an unprovenanced Asset Type from Snipe-IT")

    def _update_existing(self, obj, defaults, fieldset):
        if getattr(obj, "deleted_at", None) is not None:
            raise ValueError("Cannot update a deleted Asset Type from Snipe-IT")
        if getattr(obj, "management_kind", None) in {"core", "library"}:
            raise ValueError("Cannot update a managed Asset Type from Snipe-IT")
        if not self.context.update:
            return obj, "skipped"
        previous_data = getattr(obj, "custom_field_data", None)
        if previous_data is not None and not isinstance(previous_data, Mapping):
            raise ValueError("Existing Asset Type specifications are not a JSON object")
        if not self.context.dry_run:
            for field, value in defaults.items():
                setattr(obj, field, value)
            obj.save(update_fields=[*defaults, "updated_at"])
            if fieldset is not _OMITTED_FIELDSET:
                self._write_composition(obj, fieldset)
        return obj, "updated"

    def _create(self, model, source_id, defaults, fieldset, model_name, manufacturer):
        if not self.context.dry_run:
            obj = model.objects.create(**defaults)
            if fieldset is not _OMITTED_FIELDSET:
                self._write_composition(obj, fieldset)
        else:
            obj = model(
                id=-source_id,
                model=model_name,
                manufacturer=manufacturer,
                connector_identity=defaults["connector_identity"],
            )
        return obj, "created"

    def _upsert(self, model, row) -> tuple[object, Outcome]:
        source_id = row["id"]
        source_identity = _snipeit_identity(self.context, source_id)
        connector_identity = _connector_identity(source_identity)
        model_name = (row.get("name") or "").strip() or f"Model {source_id}"
        manufacturer = self.dependencies.manufacturers.get(_nested_id(row.get("manufacturer")))
        category = self.dependencies.categories.get(_nested_id(row.get("category")))
        if "fieldset" not in row:
            fieldset = _OMITTED_FIELDSET
        elif row["fieldset"] is None:
            fieldset = None
        else:
            fieldset_id = _nested_id(row["fieldset"])
            if fieldset_id not in self.dependencies.fieldsets:
                raise ValueError("Unresolved Snipe-IT Fieldset reference")
            fieldset = self.dependencies.fieldsets[fieldset_id]
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
            "eol_months": eol_months,
            "part_number": part_number,
            "connector_identity": connector_identity,
        }
        obj = self._find_existing(model, connector_identity, model_name, manufacturer)
        if obj is not None:
            return self._update_existing(obj, defaults, fieldset)
        return self._create(model, source_id, defaults, fieldset, model_name, manufacturer)

    def _write_composition(self, asset_type, fieldset):
        selection = ExplicitFieldsetSelectionDTO(
            identities=() if fieldset is None else (f"{fieldset.namespace}/{fieldset.slug}",)
        )
        definition, _definitions, _graph = load_prospective_definition(
            selection.identities, "asset_type", tuple(stored_values_for(asset_type))
        )
        result = set_asset_type_composition(
            actor=_actor_context_for_user(self.context.user),
            asset_type_id=asset_type.pk,
            fieldsets=selection,
            expected_resource_revision=resource_revision_for_owner(asset_type),
            expected_definition_revision=DefinitionRevision(definition.revision),
            patch=SpecificationPatchDTO(set_values={}, clear_keys=()),
        )
        _require_command_success(result)

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
