import hashlib
import math
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from django.db import migrations


class MigrationConflict(RuntimeError):
    pass


ADOPTION_FIELDS = {
    "ram_gb": {
        "name": "memory_capacity",
        "field_type": "decimal",
        "scope": "asset_type",
        "quantity_kind": "digital_information",
        "canonical_unit": "GiB",
        "minimum_value": Decimal("0.000"),
        "maximum_value": Decimal("1048576.000"),
        "decimal_scale": 3,
    },
    "storage_gb": {
        "name": "storage_capacity",
        "field_type": "decimal",
        "scope": "asset_type",
        "quantity_kind": "digital_information",
        "canonical_unit": "GiB",
        "minimum_value": Decimal("0.000"),
        "maximum_value": Decimal("1073741824.000"),
        "decimal_scale": 3,
    },
    "poe_budget_w": {
        "name": "poe_budget",
        "field_type": "decimal",
        "scope": "asset_type",
        "quantity_kind": "power",
        "canonical_unit": "W",
        "minimum_value": Decimal("0.000"),
        "maximum_value": Decimal("10000000.000"),
        "decimal_scale": 3,
    },
    "hostname": {
        "name": "hostname",
        "field_type": "text",
        "scope": "asset",
    },
    "firmware_version": {
        "name": "firmware_version",
        "field_type": "text",
        "scope": "asset",
    },
}


def _fail(code):
    raise MigrationConflict(f"issue479:{code}")


def _encoded_component(value):
    if value is None:
        return b"\x00"
    encoded = str(value).encode("utf-8")
    if not encoded:
        return b"\x01"
    return str(len(encoded)).encode("ascii") + b":" + encoded


def _tuple_bytes(kind, values):
    return b"\x1f".join(_encoded_component(value) for value in (kind, *values))


def _stable_slug(kind, visible, values):
    decomposed = unicodedata.normalize("NFKD", visible)
    ascii_value = "".join(char for char in decomposed if ord(char) < 128 and not unicodedata.combining(char))
    folded = ascii_value.casefold()
    normalized = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")
    needs_hash = normalized != folded.strip("-") or ascii_value != visible or len(normalized) > 96
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


def _scope_for(field):
    labels = {(ct.app_label, ct.model) for ct in field.object_types.all()}
    supported = {("assets", "assettype"), ("assets", "asset")}
    if not labels or not labels.issubset(supported):
        return None
    if labels == supported:
        return "both"
    if ("assets", "assettype") in labels:
        return "asset_type"
    return "asset"


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


def forward(apps, schema_editor):
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
            "scope": _scope_for(field),
            "help_text": "",
            "nullable": any(value is None for value in values),
            "mappings": [],
            "version": 1,
            "lifecycle": "active",
            "deprecated_at": None,
            "replaced_by": None,
            "managed_paths": {},
            "source_checksum": None,
            "last_reconciled_at": None,
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
            "legacy_source_key": None,
            "legacy_source_signature": None,
        }

        if adoption:
            update.update(adoption)
            update["nullable"] = False
            update["legacy_source_key"] = old_key
            update["legacy_source_signature"] = signatures[old_key]
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
                    management_kind="local",
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

    legacy_through = CustomFieldset.legacy_fields.through
    for fieldset in CustomFieldset._base_manager.using(db_alias).order_by("pk"):
        slug = _stable_slug("fieldset", fieldset.name, (fieldset.name, fieldset.pk))
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
            managed_paths={},
            source_checksum=None,
            last_reconciled_at=None,
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


def reverse(apps, schema_editor):
    _fail("reverse_refused")


class Migration(migrations.Migration):
    dependencies = [
        ("assets", "0102_asset_type_composition_schema"),
        ("extras", "0114_asset_type_definition_schema"),
    ]

    operations = [migrations.RunPython(forward, reverse)]
