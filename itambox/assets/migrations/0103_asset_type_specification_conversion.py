"""Normalized #479 specification conversion.

Converts pre-#479 definition state to the final specification vocabulary:

* renames legacy field keys to their canonical ``itambox`` identities and
  rewrites stored specification values (numbers -> canonical decimals,
  legacy select labels -> immutable choice keys);
* adopts the foundation scalar fields (``ram_gb`` / ``storage_gb`` /
  ``poe_budget_w`` / ``hostname`` / ``firmware_version``) into the core
  namespace with their final scopes and object types;
* generates stable fieldset slugs and ordered ``CustomFieldsetField``
  positions from the legacy definition state;
* backfills the ordered composition tables from the singular legacy
  ``AssetType.custom_fieldset`` column and normalizes composition positions.

Fresh installs skip every step (no legacy state exists); the migration is the
supported upgrade path for the pre-#479 baseline states.
"""

import hashlib
import math
import re
import unicodedata
from collections import Counter
from decimal import Decimal, InvalidOperation

from django.db import migrations, transaction


class MigrationConflict(RuntimeError):
    pass


EXPECTED_ADOPTION_SOURCE_PREIMAGES = {
    "ram_gb": ("RAM (GB)", "number", "", False, {"assets.assettype"}),
    "storage_gb": ("Storage (GB)", "number", "", False, {"assets.assettype"}),
    "poe_budget_w": ("PoE Budget (Watts)", "number", "", False, {"assets.assettype"}),
    "hostname": ("Hostname", "text", "", False, {"assets.asset"}),
    "firmware_version": ("Firmware Version", "text", "", False, {"assets.asset"}),
}

ADOPTION_FIELDS = {
    "ram_gb": {
        "name": "memory_capacity",
        "field_type": "decimal",
        "quantity_kind": "digital_information",
        "canonical_unit": "GiB",
        "minimum_value": Decimal("0.000"),
        "maximum_value": Decimal("1048576.000"),
        "decimal_scale": 3,
    },
    "storage_gb": {
        "name": "storage_capacity",
        "field_type": "decimal",
        "quantity_kind": "digital_information",
        "canonical_unit": "GiB",
        "minimum_value": Decimal("0.000"),
        "maximum_value": Decimal("1073741824.000"),
        "decimal_scale": 3,
    },
    "poe_budget_w": {
        "name": "poe_budget",
        "field_type": "decimal",
        "quantity_kind": "power",
        "canonical_unit": "W",
        "minimum_value": Decimal("0.000"),
        "maximum_value": Decimal("10000000.000"),
        "decimal_scale": 3,
    },
    "hostname": {
        "name": "hostname",
        "field_type": "text",
    },
    "firmware_version": {
        "name": "firmware_version",
        "field_type": "text",
    },
}

ADOPTION_POSTIMAGES = {
    "ram_gb": {
        "target": "memory_capacity",
        "label": "RAM (GB)",
        "field_type": "number",
        "object_types": {"assets.assettype"},
        "final_object_types": {"assets.asset", "assets.assettype"},
        "post_field_type": "decimal",
        "post_decimal_scale": 3,
    },
    "storage_gb": {
        "target": "storage_capacity",
        "label": "Storage (GB)",
        "field_type": "number",
        "object_types": {"assets.assettype"},
        "final_object_types": {"assets.asset", "assets.assettype"},
        "post_field_type": "decimal",
        "post_decimal_scale": 3,
    },
    "poe_budget_w": {
        "target": "poe_budget",
        "label": "PoE Budget (Watts)",
        "field_type": "number",
        "object_types": {"assets.assettype"},
        "final_object_types": {"assets.assettype"},
        "post_field_type": "decimal",
        "post_decimal_scale": 3,
    },
    "hostname": {
        "target": "hostname",
        "label": "Hostname",
        "field_type": "text",
        "object_types": {"assets.asset"},
        "final_object_types": {"assets.asset"},
    },
    "firmware_version": {
        "target": "firmware_version",
        "label": "Firmware Version",
        "field_type": "text",
        "object_types": {"assets.asset"},
        "final_object_types": {"assets.asset"},
    },
}


def _fail(code, detail=None):
    message = f"issue479:{code}"
    if detail:
        message = f"{message}:{detail}"
    raise MigrationConflict(message)


def _encoded_component(value):
    if value is None:
        return b"\x00"
    encoded = str(value).encode("utf-8")
    if not encoded:
        return b"\x01"
    return str(len(encoded)).encode("ascii") + b":" + encoded


def _tuple_bytes(kind, values):
    return b"\x1f".join(_encoded_component(value) for value in (kind, *values))


def _stable_slug(kind, visible, values, force_hash=False):
    decomposed = unicodedata.normalize("NFKD", visible)
    ascii_value = "".join(char for char in decomposed if ord(char) < 128 and not unicodedata.combining(char))
    folded = ascii_value.casefold()
    normalized = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")
    needs_hash = force_hash or normalized != folded.strip("-") or ascii_value != visible or len(normalized) > 96
    normalized = normalized[:96].rstrip("-")
    digest = hashlib.sha256(_tuple_bytes(kind, values)).hexdigest()[:12]
    if not normalized:
        return f"h{digest}"
    if needs_hash:
        return f"{normalized[:112].rstrip('-')}-h{digest}"
    return normalized


def _physical_key(source):
    normalized = unicodedata.normalize("NFKD", source).encode("ascii", "ignore").decode("ascii").casefold()
    normalized = re.sub(r"[^a-z0-9_]+", "_", normalized).strip("_")
    if not normalized or not normalized[0].isalpha():
        normalized = f"field_{normalized}"
    if len(normalized) > 64:
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:8]
        normalized = f"{normalized[:55].rstrip('_')}_{digest}"
    return normalized


def _choice_key(label, used):
    base = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii").casefold()
    base = re.sub(r"[^a-z0-9_]+", "_", base).strip("_")
    candidate = base[:63]
    if not candidate or candidate in used:
        digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:8]
        candidate = f"{(base[:54] or 'choice').rstrip('_')}_{digest}"
    ordinal = 0
    while candidate in used:
        ordinal += 1
        digest = hashlib.sha256(f"{label}\x1f{ordinal}".encode("utf-8")).hexdigest()[:8]
        candidate = f"{(base[:54] or 'choice').rstrip('_')}_{digest}"
    used.add(candidate)
    return candidate


def _signature(field, content_type_labels):
    values = [field.name, field.label, field.field_type, field.choices, "true" if field.required else "false"]
    encoded = [_encoded_component(value) for value in values]
    object_types = b"\x1e".join(label.encode("utf-8") for label in sorted(content_type_labels))
    encoded.append(_encoded_component(object_types.decode("utf-8")))
    return hashlib.sha256(b"\x1f".join(encoded)).hexdigest()


def _expected_adoption_signature(source_key):
    label, field_type, choices, required, object_types = EXPECTED_ADOPTION_SOURCE_PREIMAGES[source_key]
    values = [source_key, label, field_type, choices, "true" if required else "false"]
    encoded = [_encoded_component(value) for value in values]
    encoded.append(_encoded_component("\x1e".join(sorted(object_types))))
    return hashlib.sha256(b"\x1f".join(encoded)).hexdigest()


def _preflight_adoption_sources(fields, signatures, key_map):
    candidates = [field for field in fields if field.name in EXPECTED_ADOPTION_SOURCE_PREIMAGES]
    if not candidates:
        return
    if len(candidates) != len(EXPECTED_ADOPTION_SOURCE_PREIMAGES):
        _fail("adoption_source_set")
    by_name = {field.name: field for field in candidates}
    if len(by_name) != len(candidates):
        _fail("adoption_source_duplicate")
    for source_key, expected in EXPECTED_ADOPTION_SOURCE_PREIMAGES.items():
        field = by_name[source_key]
        if field.deleted_at is not None or signatures[source_key] != _expected_adoption_signature(source_key):
            _fail("adoption_source_signature")
        label, field_type, choices, required, object_types = expected
        actual_types = {
            f"{content_type.app_label}.{content_type.model}" for content_type in field.object_types.all()
        }
        if (field.label, field.field_type, field.choices or "", field.required, actual_types) != (
            label,
            field_type,
            choices,
            required,
            object_types,
        ):
            _fail("adoption_source_signature")
    adoption_targets = {value["name"] for value in ADOPTION_FIELDS.values()}
    if any(field.name not in EXPECTED_ADOPTION_SOURCE_PREIMAGES and key_map[field.name] in adoption_targets for field in fields):
        _fail("adoption_target_collision")


def _json_models(apps):
    models_with_data = []
    for model in apps.get_models():
        if any(field.name == "custom_field_data" for field in model._meta.concrete_fields):
            models_with_data.append(model)
    return sorted(models_with_data, key=lambda model: model._meta.label_lower)


def _values_for_key(json_models, key, db_alias):
    values = []
    for model in json_models:
        for data in model._base_manager.using(db_alias).values_list("custom_field_data", flat=True).iterator():
            if isinstance(data, dict) and key in data:
                values.append(data[key])
    return values


def _decimal_value(value):
    if isinstance(value, bool) or value is None:
        _fail("invalid_decimal_value")
    if isinstance(value, float) and not math.isfinite(value):
        _fail("invalid_decimal_value")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        _fail("invalid_decimal_value")
    if not decimal_value.is_finite():
        _fail("invalid_decimal_value")
    return decimal_value


def _scale(value):
    decimal_value = _decimal_value(value)
    return max(0, -decimal_value.as_tuple().exponent)


def _canonical_decimal(value, scale):
    decimal_value = _decimal_value(value)
    quantum = Decimal(1).scaleb(-scale)
    quantized = decimal_value.quantize(quantum)
    if quantized != decimal_value or (decimal_value.is_zero() and decimal_value.is_signed()):
        _fail("decimal_precision")
    return format(quantized, f".{scale}f")


def _rewrite_json(json_models, key_map, converters, db_alias):
    for model in json_models:
        for row in model._base_manager.using(db_alias).all().iterator():
            data = row.custom_field_data
            if not isinstance(data, dict):
                _fail("invalid_json_store")
            rewritten = dict(data)
            changed = False
            for old_key, new_key in key_map.items():
                if old_key not in data:
                    continue
                value = converters[old_key](data[old_key])
                if new_key != old_key and new_key in data:
                    _fail("key_collision")
                rewritten.pop(old_key, None)
                rewritten[new_key] = value
                changed = changed or new_key != old_key or value != data[old_key]
            if changed:
                model._base_manager.using(db_alias).filter(pk=row.pk).update(custom_field_data=rewritten)


def _validate_decimal_values(values, scale):
    quantum = Decimal(1).scaleb(-scale)
    for value in values:
        if value is None or isinstance(value, bool):
            _fail("adoption_value")
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            _fail("adoption_value")
        if not decimal_value.is_finite() or decimal_value.is_zero() and decimal_value.is_signed():
            _fail("adoption_value")
        try:
            if decimal_value.quantize(quantum) != decimal_value:
                _fail("adoption_value")
        except InvalidOperation:
            _fail("adoption_value")


def _actual_object_types(field):
    return {f"{content_type.app_label}.{content_type.model}" for content_type in field.object_types.all()}


def convert_definitions(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    CustomField = apps.get_model("extras", "CustomField")
    CustomFieldChoiceSet = apps.get_model("extras", "CustomFieldChoiceSet")
    CustomFieldChoice = apps.get_model("extras", "CustomFieldChoice")
    CustomFieldset = apps.get_model("extras", "CustomFieldset")
    CustomFieldsetField = apps.get_model("extras", "CustomFieldsetField")
    json_models = _json_models(apps)

    fields = list(CustomField._base_manager.using(db_alias).prefetch_related("object_types").order_by("pk"))
    names = [field.name for field in fields]
    if len(names) != len(set(names)):
        _fail("duplicate_field_key")

    key_map = {}
    used_keys = set()
    signatures = {}
    for field in fields:
        labels = [f"{ct.app_label}.{ct.model}" for ct in field.object_types.all()]
        signatures[field.name] = _signature(field, labels)
        target = ADOPTION_FIELDS.get(field.name, {}).get("name") or _physical_key(field.name)
        if target in used_keys:
            digest = hashlib.sha256(signatures[field.name].encode("ascii")).hexdigest()[:8]
            target = f"{target[:55].rstrip('_')}_{digest}"
        if target in used_keys:
            _fail("key_collision")
        used_keys.add(target)
        key_map[field.name] = target

    _preflight_adoption_sources(fields, signatures, key_map)
    for field in fields:
        adoption = ADOPTION_FIELDS.get(field.name)
        if adoption and adoption["field_type"] == "decimal":
            values = _values_for_key(json_models, field.name, db_alias)
            if any(value is None or _scale(value) > adoption["decimal_scale"] for value in values):
                _fail("adoption_decimal_value")

    converters = {}
    updates = {}
    for field in fields:
        old_key = field.name
        values = _values_for_key(json_models, old_key, db_alias)
        adoption = ADOPTION_FIELDS.get(old_key)
        update = {
            "name": key_map[old_key],
            "namespace": "local",
            "management_kind": "local",
            "help_text": "",
            "nullable": any(value is None for value in values),
            "mappings": [],
            "version": 1,
            "lifecycle": "active",
            "deprecated_at": None,
            "replaced_by": None,
            "choice_set_id": None,
            "quantity_kind": None,
            "canonical_unit": None,
            "minimum_value": None,
            "maximum_value": None,
            "regex": None,
            "decimal_scale": None,
            "max_values": None,
            "text_max_length": None,
            "validation_rule": None,
        }

        if adoption:
            update.update(adoption)
            update["nullable"] = False
            update["namespace"] = "itambox"
            update["management_kind"] = "core"
            update["required"] = False
            if adoption["field_type"] == "decimal":
                if any(value is None or _scale(value) > adoption["decimal_scale"] for value in values):
                    _fail("adoption_decimal_value")
                converters[old_key] = lambda value, scale=adoption["decimal_scale"]: _canonical_decimal(value, scale)
            else:
                converters[old_key] = lambda value: value
        elif field.field_type == "number":
            non_null_values = [value for value in values if value is not None]
            scale = max((_scale(value) for value in non_null_values), default=0)
            if scale > 6:
                _fail("decimal_scale")
            update.update({"field_type": "decimal", "decimal_scale": scale})
            converters[old_key] = lambda value, scale=scale: None if value is None else _canonical_decimal(value, scale)
        elif field.field_type == "select":
            labels = [line.strip() for line in (field.choices or "").splitlines() if line.strip()]
            if len(labels) > 64 or len(labels) != len(set(labels)):
                _fail("choice_definition")
            slug = _stable_slug("choice-set", f"{key_map[old_key]}-choices", (key_map[old_key], field.pk))
            choice_set = CustomFieldChoiceSet._base_manager.using(db_alias).create(
                namespace="local",
                slug=slug,
                label=f"{field.label} choices",
                management_kind="local",
                version=1,
                lifecycle="active",
            )
            label_to_key = {}
            used_choice_keys = set()
            for index, label in enumerate(labels, start=1):
                key = _choice_key(label, used_choice_keys)
                label_to_key[label] = key
                CustomFieldChoice._base_manager.using(db_alias).create(
                    choice_set_id=choice_set.pk,
                    key=key,
                    label=label,
                    position=index * 10,
                    version=1,
                    lifecycle="active",
                )

            def convert_choice(value, choices=label_to_key):
                if value is None or value == "":
                    return None
                normalized = value.strip() if isinstance(value, str) else value
                if normalized not in choices:
                    _fail("unknown_choice")
                return choices[normalized]

            update.update(
                {
                    "field_type": "single-select",
                    "choice_set_id": choice_set.pk,
                    "max_values": 1,
                    "nullable": update["nullable"] or any(value == "" for value in values),
                }
            )
            converters[old_key] = convert_choice
        else:
            legacy_type_map = {"text": "text", "date": "date", "boolean": "boolean"}
            if field.field_type not in legacy_type_map:
                _fail("unknown_field_type")
            update["field_type"] = legacy_type_map[field.field_type]
            converters[old_key] = lambda value: value
        updates[field.pk] = update

    _rewrite_json(json_models, key_map, converters, db_alias)
    for field in fields:
        CustomField._base_manager.using(db_alias).filter(pk=field.pk).update(**updates[field.pk])

    adopted = [field for field in fields if field.name in EXPECTED_ADOPTION_SOURCE_PREIMAGES]
    if adopted:
        through = CustomField.object_types.through
        for source_key, definition in ADOPTION_POSTIMAGES.items():
            rows = list(CustomField._base_manager.using(db_alias).filter(name=definition["target"]).prefetch_related("object_types"))
            if len(rows) != 1:
                _fail("adoption_target_collision")
            field = rows[0]
            expected_field_type = definition.get("post_field_type", definition["field_type"])
            expected_decimal_scale = definition.get("post_decimal_scale")
            if (
                field.label,
                field.field_type,
                field.choices or "",
                field.required,
                field.namespace,
                field.management_kind,
                _actual_object_types(field),
            ) != (
                definition["label"],
                expected_field_type,
                "",
                False,
                "itambox",
                "core",
                definition["object_types"],
            ) or field.decimal_scale != expected_decimal_scale:
                _fail("adoption_postimage")
            if source_key in {"ram_gb", "storage_gb", "poe_budget_w"}:
                _validate_decimal_values(_values_for_key(json_models, definition["target"], db_alias), 3)
            through._base_manager.using(db_alias).filter(customfield_id=field.pk).delete()
            content_type_ids = [
                content_type.pk
                for content_type in apps.get_model("contenttypes", "ContentType")
                ._base_manager.using(db_alias)
                .filter(
                    app_label="assets",
                    model__in=[identity.split(".", 1)[1] for identity in definition["final_object_types"]],
                )
            ]
            through._base_manager.using(db_alias).bulk_create(
                [through(customfield_id=field.pk, contenttype_id=content_type_id) for content_type_id in content_type_ids]
            )

    legacy_through = CustomFieldset.legacy_fields.through
    fieldsets = list(CustomFieldset._base_manager.using(db_alias).order_by("pk"))
    base_slugs = {
        fieldset.pk: _stable_slug("fieldset", fieldset.name, (fieldset.name, fieldset.pk)) for fieldset in fieldsets
    }
    slug_counts = Counter(base_slugs.values())
    slugs = {
        fieldset.pk: _stable_slug(
            "fieldset",
            fieldset.name,
            (fieldset.name, fieldset.pk),
            force_hash=slug_counts[base_slugs[fieldset.pk]] > 1,
        )
        for fieldset in fieldsets
    }
    if len(slugs.values()) != len(set(slugs.values())):
        _fail("fieldset_slug_collision")

    for fieldset in fieldsets:
        slug = slugs[fieldset.pk]
        CustomFieldset._base_manager.using(db_alias).filter(pk=fieldset.pk).update(
            namespace="local",
            slug=slug,
            label=fieldset.name,
            description="",
            management_kind="local",
            version=1,
            lifecycle="active",
            deprecated_at=None,
            replaced_by=None,
        )
        legacy_field_ids = legacy_through._base_manager.using(db_alias).filter(
            customfieldset_id=fieldset.pk
        ).order_by("pk").values_list("customfield_id", flat=True)
        for index, field_id in enumerate(legacy_field_ids, start=1):
            CustomFieldsetField._base_manager.using(db_alias).create(
                fieldset_id=fieldset.pk,
                custom_field_id=field_id,
                position=index * 10,
            )


def backfill_composition(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    AssetType = apps.get_model("assets", "AssetType")
    AssetTypeFieldset = apps.get_model("assets", "AssetTypeFieldset")

    expected = AssetType._base_manager.using(db_alias).filter(custom_fieldset_id__isnull=False).count()
    created = 0
    for asset_type_id, fieldset_id in (
        AssetType._base_manager.using(db_alias)
        .filter(custom_fieldset_id__isnull=False)
        .order_by("pk")
        .values_list("pk", "custom_fieldset_id")
    ):
        _, was_created = AssetTypeFieldset._base_manager.using(db_alias).get_or_create(
            asset_type_id=asset_type_id,
            fieldset_id=fieldset_id,
            defaults={"position": 10},
        )
        created += int(was_created)
    if created != expected:
        _fail("composition_count")


def _preflight_rows(model, owner_field, member_field, db_alias):
    owner_ids = model._base_manager.using(db_alias).values_list(owner_field, flat=True).distinct()
    for owner_id in owner_ids:
        rows = list(
            model._base_manager.using(db_alias)
            .filter(**{owner_field: owner_id})
            .order_by("position", member_field, "pk")
            .values("pk", "position", member_field)
        )
        if len(rows) > 1_000_000:
            _fail("position_cardinality", f"{model._meta.db_table}:{owner_id}")
        seen_members = set()
        seen_positions = set()
        for row in rows:
            position = row["position"]
            member_id = row[member_field]
            if not isinstance(position, int) or not 1 <= position <= 1_000_000:
                _fail("invalid_position", f"{model._meta.db_table}:{row['pk']}:{position}")
            if member_id in seen_members:
                _fail("duplicate_member", f"{model._meta.db_table}:{owner_id}:{member_id}")
            if position in seen_positions:
                _fail("duplicate_position", f"{model._meta.db_table}:{owner_id}:{position}")
            seen_members.add(member_id)
            seen_positions.add(position)


def _renumber(model, owner_field, member_sort_field, db_alias):
    owner_ids = model._base_manager.using(db_alias).values_list(owner_field, flat=True).distinct()
    for owner_id in owner_ids:
        rows = list(
            model._base_manager.using(db_alias)
            .filter(**{owner_field: owner_id})
            .order_by("position", member_sort_field, "pk")
        )
        for position, row in enumerate(rows, start=1):
            model._base_manager.using(db_alias).filter(pk=row.pk).update(position=position)


def normalize_composition(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    AssetTypeFieldset = apps.get_model("assets", "AssetTypeFieldset")
    CategoryDefaultFieldset = apps.get_model("assets", "CategoryDefaultFieldset")
    _preflight_rows(AssetTypeFieldset, "asset_type_id", "fieldset_id", db_alias)
    _preflight_rows(CategoryDefaultFieldset, "category_id", "fieldset_id", db_alias)
    _renumber(AssetTypeFieldset, "asset_type_id", "fieldset_id", db_alias)
    _renumber(CategoryDefaultFieldset, "category_id", "fieldset_id", db_alias)


def refuse_reverse(apps, schema_editor):
    _fail("reverse_refused")


class Migration(migrations.Migration):
    dependencies = [
        ("assets", "0102_asset_type_composition_schema"),
        ("extras", "0114_asset_type_definition_library_schema"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.RunPython(convert_definitions, reverse_code=refuse_reverse),
        migrations.RunPython(backfill_composition, reverse_code=refuse_reverse),
        migrations.RunPython(normalize_composition, reverse_code=refuse_reverse),
        migrations.RunSQL("SET CONSTRAINTS ALL IMMEDIATE", reverse_sql=migrations.RunSQL.noop),
    ]
