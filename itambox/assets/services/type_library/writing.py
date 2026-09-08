"""Real Django persistence and effective export projection for libraries.

No migration is introduced here.  The writer uses the existing immutable
Library/Release models and managed-definition relations.  Unsupported history
that the current model cannot persist is rejected rather than represented by a
new unapproved DTO or a lossy flag.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from decimal import Decimal
from typing import Any

from assets.services.type_library.planning import LibraryPlan
from assets.services.type_library_validation import ValidatedLibraryDocument

_SLUG_RE = re.compile(r"[^a-z0-9-]+")


class LibraryWriteError(RuntimeError):
    """A transactional library write failure."""

    def __init__(self, code: str, path: tuple[str | int, ...] = ()):
        self.code = code
        self.path = path
        super().__init__(code)


def write_library_document(
    library: Any,
    incoming: ValidatedLibraryDocument,
    plan: LibraryPlan,
    using: str,
) -> tuple[str, ...]:
    """Persist a complete release/snapshot plan inside the caller's transaction."""

    from extras.models import SpecificationLibraryRelease

    source, definitions = _source_and_definitions(incoming)
    if incoming.kind not in {"itambox.type-library.release", "itambox.type-library.snapshot"}:
        raise LibraryWriteError("INVALID_EXPORT_KIND")
    if plan.namespace != library.namespace:
        raise LibraryWriteError("OWNERSHIP_CONFLICT", ("library", "namespace"))
    if not plan.can_apply:
        raise LibraryWriteError("CONFLICT")
    if _is_noop(library, plan):
        return ()

    release = (
        SpecificationLibraryRelease.objects.using(using)
        .filter(library_id=library.pk, sequence=plan.incoming_release)
        .first()
    )
    if release is not None:
        if release.semantic_digest != plan.source_digest:
            raise LibraryWriteError("EQUIVOCATION", ("library", "release"))
    else:
        release = SpecificationLibraryRelease(
            library=library,
            sequence=plan.incoming_release,
            semantic_digest=plan.source_digest,
            source_document=deepcopy(source),
        )
        release.save(using=using)

    _WriteContext(library=library, definitions=definitions, using=using).write_all()
    library.accept_release(release, using=using)
    return tuple(action.action_id for action in plan.actions if action.decision == "take_upstream")


def effective_definitions_from_library(library: Any) -> dict[str, list[dict[str, Any]]]:
    """Serialize current library-owned definitions without tenant Asset rows."""

    accepted = library.accepted_release
    if accepted is None:
        return {
            "choice_sets": [],
            "fields": [],
            "fieldsets": [],
            "categories": [],
            "manufacturers": [],
            "asset_types": [],
        }
    definitions = deepcopy(accepted.source_document["definitions"])
    context = _ReadContext(library=library, definitions=definitions)
    context.read_choice_sets()
    context.read_fields()
    context.read_fieldsets()
    context.read_asset_types()
    return definitions


def _source_and_definitions(incoming: ValidatedLibraryDocument) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    document = incoming.normalized_document
    if incoming.kind == "itambox.type-library.release":
        return deepcopy(document), deepcopy(document["definitions"])
    if incoming.kind == "itambox.type-library.snapshot":
        return deepcopy(document["upstream"]), deepcopy(document["effective_definitions"])
    raise LibraryWriteError("INVALID_EXPORT_KIND")


def _is_noop(library: Any, plan: LibraryPlan) -> bool:
    accepted = library.accepted_release
    return (
        accepted is not None
        and accepted.sequence == plan.incoming_release
        and accepted.semantic_digest == plan.source_digest
        and not any(action.decision == "take_upstream" for action in plan.actions)
    )


class _WriteContext:
    def __init__(self, *, library: Any, definitions: Mapping[str, Any], using: str):
        self.library = library
        self.definitions = definitions
        self.using = using
        self.choice_sets: dict[str, Any] = {}
        self.fields: dict[str, Any] = {}
        self.fieldsets: dict[str, Any] = {}
        self.categories: dict[str, Any] = {}
        self.manufacturers: dict[str, Any] = {}

    def write_all(self) -> None:
        self.write_choice_sets()
        self.write_fields()
        self.write_fieldsets()
        self.write_references()
        self.write_asset_types()
        self.retire_missing()

    def write_choice_sets(self) -> None:
        from django.utils import timezone

        from extras.models import CustomFieldChoice, CustomFieldChoiceSet

        for item in self.definitions.get("choice_sets", []):
            namespace, slug = _split_identity(item["id"])
            choice_set = CustomFieldChoiceSet.objects.using(self.using).filter(namespace=namespace, slug=slug).first()
            if choice_set is None:
                choice_set = CustomFieldChoiceSet(
                    namespace=namespace,
                    slug=slug,
                    label=item["label"],
                    library=self.library,
                    management_kind=CustomFieldChoiceSet.MANAGEMENT_LIBRARY,
                )
                choice_set.save(using=self.using)
            else:
                _save_fields(choice_set, {"label": item["label"], "lifecycle": item.get("lifecycle", "active")}, self.using)
            self.choice_sets[item["id"]] = choice_set
            existing = list(
                CustomFieldChoice.objects.using(self.using)
                .filter(choice_set_id=choice_set.pk)
                .order_by("position", "pk")
            )
            temporary_base = (
                max((choice.position for choice in existing), default=0)
                + len(existing)
                + len(item.get("choices", []))
                + 1
            )
            for offset, choice in enumerate(existing):
                if choice.position != temporary_base + offset:
                    choice.position = temporary_base + offset
                    choice.save(using=self.using, update_fields=["position"])
            existing_by_key = {choice.key: choice for choice in existing}
            desired_keys = set()
            for position, choice_data in enumerate(item.get("choices", []), start=1):
                key = choice_data["key"]
                desired_keys.add(key)
                choice = existing_by_key.get(key)
                lifecycle = choice_data.get("lifecycle", "active")
                values = {
                    "label": choice_data["label"],
                    "position": position,
                    "replaced_by": choice_data.get("replaced_by"),
                    "lifecycle": lifecycle,
                    "deprecated_at": (
                        choice.deprecated_at
                        if choice is not None and choice.lifecycle == "deprecated" and lifecycle == "deprecated"
                        else timezone.now() if lifecycle == "deprecated" else None
                    ),
                }
                if choice is None:
                    choice = CustomFieldChoice(choice_set=choice_set, key=key, **values)
                    choice.save(using=self.using)
                else:
                    _save_fields(choice, values, self.using)
            retained_position = len(desired_keys) + 1
            for choice in existing:
                if choice.key in desired_keys:
                    continue
                values = {"position": retained_position, "lifecycle": "deprecated"}
                if choice.lifecycle != "deprecated" or choice.deprecated_at is None:
                    values["deprecated_at"] = timezone.now()
                _save_fields(choice, values, self.using)
                retained_position += 1

    def write_fields(self) -> None:
        from django.contrib.contenttypes.models import ContentType

        from assets.models.asset import Asset
        from assets.models.catalog import AssetType
        from extras.models import CustomField

        asset_type_content_type = ContentType.objects.db_manager(self.using).get_for_model(Asset)
        type_content_type = ContentType.objects.db_manager(self.using).get_for_model(AssetType)
        for item in self.definitions.get("fields", []):
            identity = f"{item['namespace']}/{item['key']}"
            field = CustomField.objects.using(self.using).filter(namespace=item["namespace"], name=item["key"]).first()
            values = _field_values(item, self.choice_sets)
            if field is None:
                field = CustomField(
                    namespace=item["namespace"],
                    name=item["key"],
                    library=self.library,
                    management_kind=CustomField.MANAGEMENT_LIBRARY,
                    **values,
                )
                field.save(using=self.using)
            else:
                _save_fields(field, values, self.using)
            target_types = []
            if "asset" in item.get("targets", []):
                target_types.append(asset_type_content_type)
            if "asset_type" in item.get("targets", []):
                target_types.append(type_content_type)
            field.object_types.set(target_types)
            self.fields[identity] = field

    def write_fieldsets(self) -> None:
        from extras.models import CustomFieldset, CustomFieldsetField

        for item in self.definitions.get("fieldsets", []):
            namespace, slug = _split_identity(item["id"])
            fieldset = CustomFieldset.objects.using(self.using).filter(namespace=namespace, slug=slug).first()
            values = {
                "label": item["label"],
                "description": item.get("description", ""),
                "lifecycle": item.get("lifecycle", "active"),
                "library": self.library,
                "management_kind": CustomFieldset.MANAGEMENT_LIBRARY,
            }
            if fieldset is None:
                fieldset = CustomFieldset(namespace=namespace, slug=slug, **values)
                fieldset.save(using=self.using)
            else:
                _save_fields(fieldset, values, self.using)
            memberships = list(
                fieldset.field_memberships.using(self.using).order_by("position", "custom_field_id")
            )
            desired = [self.fields[identity].pk for identity in item.get("fields", [])]
            if [row.custom_field_id for row in memberships] != desired:
                fieldset.field_memberships.using(self.using).all().delete()
                CustomFieldsetField.objects.using(self.using).bulk_create(
                    [
                        CustomFieldsetField(fieldset=fieldset, custom_field_id=field_id, position=position)
                        for position, field_id in enumerate(desired, start=1)
                    ]
                )
            self.fieldsets[item["id"]] = fieldset

    def write_references(self) -> None:
        from assets.models.catalog import Category, Manufacturer

        for item in self.definitions.get("manufacturers", []):
            slug = _split_identity(item["id"])[1]
            manufacturer = Manufacturer.all_objects.using(self.using).filter(slug=slug).first()
            if manufacturer is None:
                manufacturer = Manufacturer(
                    name=item["label"],
                    slug=slug,
                    description=item.get("description", ""),
                )
                manufacturer.save(using=self.using)
            self.manufacturers[item["id"]] = manufacturer
        for item in self.definitions.get("categories", []):
            slug = _split_identity(item["id"])[1]
            category = Category.all_objects.using(self.using).filter(slug=slug).first()
            if category is None:
                category = Category(
                    name=item["label"],
                    slug=slug,
                    description=item.get("description", ""),
                    applies_to={value: True for value in item.get("applies_to", [])},
                )
                category.save(using=self.using)
            self.categories[item["id"]] = category

    def write_asset_types(self) -> None:
        from assets.models.catalog import AssetType, AssetTypeFieldset

        for item in self.definitions.get("asset_types", []):
            namespace, definition_key = _split_identity(item["id"])
            asset_type = (
                AssetType.all_objects.using(self.using)
                .filter(library_id=self.library.pk, library_definition_key=definition_key)
                .first()
            )
            manufacturer = self.manufacturers.get(item["manufacturer"])
            category = self.categories.get(item.get("category"))
            if manufacturer is None:
                raise LibraryWriteError("REFERENCE_UNAVAILABLE", ("asset_types", definition_key, "manufacturer"))
            values = {
                "manufacturer": manufacturer,
                "model": item["model"],
                "part_number": item.get("part_number", ""),
                "ean": item.get("gtin") or "",
                "region": item.get("region", ""),
                "configuration": item.get("configuration", ""),
                "description": item.get("description", ""),
                "category": category,
                "custom_field_data": deepcopy(item.get("specifications", {})),
                "lifecycle": item.get("lifecycle", "active"),
                "management_kind": AssetType.MANAGEMENT_LIBRARY,
                "library": self.library,
                "library_definition_key": definition_key,
            }
            if asset_type is None:
                values["slug"] = _asset_type_slug(namespace, definition_key)
                asset_type = AssetType(**values)
                asset_type.save(using=self.using)
            else:
                _save_fields(asset_type, values, self.using)
            desired = [self.fieldsets[identity].pk for identity in item.get("fieldsets", [])]
            memberships = list(
                asset_type.fieldset_memberships.using(self.using).order_by("position", "fieldset_id")
            )
            if [row.fieldset_id for row in memberships] != desired:
                asset_type.fieldset_memberships.using(self.using).all().delete()
                AssetTypeFieldset.objects.using(self.using).bulk_create(
                    [
                        AssetTypeFieldset(asset_type=asset_type, fieldset_id=fieldset_id, position=position)
                        for position, fieldset_id in enumerate(desired, start=1)
                    ]
                )

    def retire_missing(self) -> None:
        from django.utils import timezone

        from assets.models.catalog import AssetType
        from extras.models import CustomField, CustomFieldChoice, CustomFieldChoiceSet, CustomFieldset

        now = timezone.now()
        ids = {
            "fields": {f"{item['namespace']}/{item['key']}" for item in self.definitions.get("fields", [])},
            "fieldsets": set(item["id"] for item in self.definitions.get("fieldsets", [])),
            "choice_sets": set(item["id"] for item in self.definitions.get("choice_sets", [])),
            "asset_types": set(item["id"] for item in self.definitions.get("asset_types", [])),
        }
        for queryset, section, identity in (
            (CustomField.objects.using(self.using).filter(library_id=self.library.pk), "fields", _field_identity),
            (CustomFieldset.objects.using(self.using).filter(library_id=self.library.pk), "fieldsets", _object_identity),
            (
                CustomFieldChoiceSet.objects.using(self.using).filter(library_id=self.library.pk),
                "choice_sets",
                _object_identity,
            ),
            (AssetType.all_objects.using(self.using).filter(library_id=self.library.pk), "asset_types", _asset_identity),
        ):
            for instance in queryset:
                if identity(instance) in ids[section] and instance.lifecycle == "active":
                    continue
                if instance.lifecycle != "deprecated":
                    _save_fields(instance, {"lifecycle": "deprecated", "deprecated_at": now}, self.using)

        for choice_set in CustomFieldChoiceSet.objects.using(self.using).filter(library_id=self.library.pk):
            definition = next(
                (
                    item
                    for item in self.definitions.get("choice_sets", [])
                    if item.get("id") == f"{choice_set.namespace}/{choice_set.slug}"
                ),
                None,
            )
            retained_keys = {choice["key"] for choice in (definition or {}).get("choices", [])}
            for choice in CustomFieldChoice.objects.using(self.using).filter(choice_set_id=choice_set.pk):
                if choice.key not in retained_keys and choice.lifecycle != "deprecated":
                    _save_fields(
                        choice,
                        {"lifecycle": "deprecated", "deprecated_at": now},
                        self.using,
                    )


class _ReadContext:
    def __init__(self, *, library: Any, definitions: dict[str, list[dict[str, Any]]]):
        self.library = library
        self.definitions = definitions

    def read_choice_sets(self) -> None:
        for choice_set in self.library.choice_sets.all().prefetch_related("choices"):
            item = _find_definition(self.definitions["choice_sets"], f"{choice_set.namespace}/{choice_set.slug}")
            if item is None:
                continue
            item["label"] = choice_set.label
            item["lifecycle"] = choice_set.lifecycle
            choices = sorted(choice_set.choices.all(), key=lambda row: (row.position, row.key))
            item["choices"] = [
                {
                    "key": choice.key,
                    "label": choice.label,
                    "lifecycle": choice.lifecycle,
                    **({"replaced_by": choice.replaced_by} if choice.replaced_by else {}),
                }
                for choice in choices
            ]

    def read_fields(self) -> None:
        for field in self.library.fields.all().prefetch_related("object_types"):
            identity = f"{field.namespace}/{field.name}"
            item = _find_field(self.definitions["fields"], identity)
            if item is None:
                continue
            item.update(
                {
                    "label": field.label,
                    "help_text": field.help_text,
                    "field_type": field.field_type,
                    "activation": field.activation,
                    "required": field.required,
                    "nullable": field.nullable,
                    "lifecycle": field.lifecycle,
                    "targets": sorted(
                        {
                            "asset" if content_type.model == "asset" else "asset_type"
                            for content_type in field.object_types.all()
                            if content_type.model in {"asset", "assettype"}
                        }
                    ),
                }
            )

    def read_fieldsets(self) -> None:
        for fieldset in self.library.fieldsets.all().prefetch_related("field_memberships__custom_field"):
            item = _find_definition(self.definitions["fieldsets"], f"{fieldset.namespace}/{fieldset.slug}")
            if item is None:
                continue
            item["label"] = fieldset.label
            item["description"] = fieldset.description
            item["lifecycle"] = fieldset.lifecycle
            item["fields"] = [
                f"{row.custom_field.namespace}/{row.custom_field.name}"
                for row in fieldset.field_memberships.all().order_by("position", "custom_field_id")
            ]

    def read_asset_types(self) -> None:
        for asset_type in self.library.asset_types.all().select_related("manufacturer", "category").prefetch_related(
            "fieldset_memberships__fieldset"
        ):
            identity = f"{self.library.namespace}/{asset_type.library_definition_key}"
            item = _find_definition(self.definitions["asset_types"], identity)
            if item is None:
                continue
            item.update(
                {
                    "model": asset_type.model,
                    "part_number": asset_type.part_number,
                    "gtin": asset_type.ean or None,
                    "region": asset_type.region,
                    "configuration": asset_type.configuration,
                    "description": asset_type.description,
                    "lifecycle": asset_type.lifecycle,
                    "specifications": deepcopy(asset_type.custom_field_data),
                    "fieldsets": [
                        f"{row.fieldset.namespace}/{row.fieldset.slug}"
                        for row in asset_type.fieldset_memberships.all().order_by("position", "fieldset_id")
                    ],
                }
            )


def _field_values(item: Mapping[str, Any], choice_sets: Mapping[str, Any]) -> dict[str, Any]:
    validation = item.get("validation", {})
    choice_set = choice_sets.get(item.get("choice_set"))
    return {
        "label": item["label"],
        "help_text": item.get("help_text", ""),
        "field_type": item["field_type"],
        "activation": item.get("activation", "composed"),
        "required": item.get("required", False),
        "nullable": item.get("nullable", False),
        "quantity_kind": item.get("quantity_kind"),
        "canonical_unit": item.get("canonical_unit"),
        "minimum_value": _decimal_or_none(validation.get("minimum")),
        "maximum_value": _decimal_or_none(validation.get("maximum")),
        "regex": validation.get("pattern"),
        "decimal_scale": validation.get("scale"),
        "max_values": validation.get("max_values"),
        "text_max_length": validation.get("max_length"),
        "validation_rule": validation.get("rule"),
        "mappings": deepcopy(item.get("mappings", [])),
        "choice_set": choice_set,
        "lifecycle": item.get("lifecycle", "active"),
    }


def _save_fields(instance: Any, values: Mapping[str, Any], using: str) -> None:
    changed = [field for field, value in values.items() if getattr(instance, field) != value]
    if not changed:
        return
    for field in changed:
        setattr(instance, field, values[field])
    instance.save(using=using, update_fields=changed)


def _split_identity(identity: str) -> tuple[str, str]:
    return identity.split("/", 1)


def _asset_type_slug(namespace: str, key: str) -> str:
    value = _SLUG_RE.sub("-", f"{namespace}-{key}").strip("-")
    return value[:255]


def _decimal_or_none(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _field_identity(field: Any) -> str:
    return f"{field.namespace}/{field.name}"


def _asset_identity(asset_type: Any) -> str:
    return f"{asset_type.library.namespace}/{asset_type.library_definition_key}"


def _object_identity(instance: Any) -> str:
    return f"{instance.namespace}/{instance.slug}"


def _find_definition(items: list[dict[str, Any]], identity: str) -> dict[str, Any] | None:
    return next((item for item in items if item.get("id") == identity), None)


def _find_field(items: list[dict[str, Any]], identity: str) -> dict[str, Any] | None:
    return next((item for item in items if f"{item.get('namespace')}/{item.get('key')}" == identity), None)


__all__ = [
    "LibraryWriteError",
    "effective_definitions_from_library",
    "write_library_document",
]
