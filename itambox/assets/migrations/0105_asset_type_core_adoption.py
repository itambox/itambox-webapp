from decimal import Decimal, InvalidOperation

from django.db import migrations


class MigrationConflict(RuntimeError):
    pass


SOURCE_PREIMAGES = {
    "ram_gb": {
        "target": "memory_capacity",
        "label": "RAM (GB)",
        "field_type": "number",
        "scope": "asset_type",
        "object_types": {"assets.assettype"},
        "final_scope": "both",
        "final_object_types": {"assets.asset", "assets.assettype"},
        "post_field_type": "decimal",
        "post_decimal_scale": 3,
    },
    "storage_gb": {
        "target": "storage_capacity",
        "label": "Storage (GB)",
        "field_type": "number",
        "scope": "asset_type",
        "object_types": {"assets.assettype"},
        "final_scope": "both",
        "final_object_types": {"assets.asset", "assets.assettype"},
        "post_field_type": "decimal",
        "post_decimal_scale": 3,
    },
    "poe_budget_w": {
        "target": "poe_budget",
        "label": "PoE Budget (Watts)",
        "field_type": "number",
        "scope": "asset_type",
        "object_types": {"assets.assettype"},
        "final_scope": "asset_type",
        "final_object_types": {"assets.assettype"},
        "post_field_type": "decimal",
        "post_decimal_scale": 3,
    },
    "hostname": {
        "target": "hostname",
        "label": "Hostname",
        "field_type": "text",
        "scope": "asset",
        "object_types": {"assets.asset"},
        "final_scope": "asset",
        "final_object_types": {"assets.asset"},
    },
    "firmware_version": {
        "target": "firmware_version",
        "label": "Firmware Version",
        "field_type": "text",
        "scope": "asset",
        "object_types": {"assets.asset"},
        "final_scope": "asset",
        "final_object_types": {"assets.asset"},
    },
}


def _fail(code):
    raise MigrationConflict(f"issue479:{code}")


def _encoded_component(value):
    if value is None:
        return b"\x00"
    raw = str(value).encode("utf-8")
    if not raw:
        return b"\x01"
    return str(len(raw)).encode("ascii") + b":" + raw


def _source_signature(source_key, definition):
    values = [source_key, definition["label"], definition["field_type"], "", "false"]
    encoded = [_encoded_component(value) for value in values]
    encoded.append(_encoded_component("\x1e".join(sorted(definition["object_types"]))))
    import hashlib

    return hashlib.sha256(b"\x1f".join(encoded)).hexdigest()


def _json_models(apps):
    return sorted(
        [
            model
            for model in apps.get_models()
            if any(field.name == "custom_field_data" for field in model._meta.concrete_fields)
        ],
        key=lambda model: model._meta.label_lower,
    )


def _values_for_key(json_models, key, db_alias):
    values = []
    for model in json_models:
        for data in model._base_manager.using(db_alias).values_list("custom_field_data", flat=True).iterator():
            if isinstance(data, dict) and key in data:
                values.append(data[key])
    return values


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


def forward(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    CustomField = apps.get_model("extras", "CustomField")
    json_models = _json_models(apps)
    fields = list(CustomField._base_manager.using(db_alias).prefetch_related("object_types").order_by("pk"))
    candidates = [field for field in fields if field.legacy_source_key in SOURCE_PREIMAGES]
    if not candidates:
        return
    if len(candidates) != len(SOURCE_PREIMAGES):
        _fail("adoption_target_set")

    by_source = {field.legacy_source_key: field for field in candidates}
    if len(by_source) != len(candidates):
        _fail("adoption_marker_duplicate")
    for source_key, definition in SOURCE_PREIMAGES.items():
        field = by_source.get(source_key)
        if field is None or field.deleted_at is not None:
            _fail("adoption_marker_missing")
        if field.name != definition["target"]:
            _fail("adoption_target_name")
        if field.legacy_source_signature != _source_signature(source_key, definition):
            _fail("adoption_source_signature")
        expected_field_type = definition.get("post_field_type", definition["field_type"])
        expected_decimal_scale = definition.get("post_decimal_scale")
        if (
            field.label,
            field.field_type,
            field.choices or "",
            field.required,
            field.scope,
            _actual_object_types(field),
        ) != (
            definition["label"],
            expected_field_type,
            "",
            False,
            definition["scope"],
            definition["object_types"],
        ) or field.decimal_scale != expected_decimal_scale:
            _fail("adoption_postimage")

    targets = [definition["target"] for definition in SOURCE_PREIMAGES.values()]
    if any(CustomField._base_manager.using(db_alias).filter(name=target).count() != 1 for target in targets):
        _fail("adoption_target_collision")
    for source_key, definition in SOURCE_PREIMAGES.items():
        if source_key in {"ram_gb", "storage_gb", "poe_budget_w"}:
            _validate_decimal_values(_values_for_key(json_models, definition["target"], db_alias), 3)

    through = CustomField.object_types.through
    for source_key, definition in SOURCE_PREIMAGES.items():
        field = by_source[source_key]
        CustomField._base_manager.using(db_alias).filter(pk=field.pk).update(
            namespace="itambox",
            management_kind="core",
            scope=definition["final_scope"],
            required=False,
            nullable=False,
            choice_set_id=None,
            mappings=[],
            version=1,
            lifecycle="active",
            deprecated_at=None,
            replaced_by=None,
            managed_paths={},
            source_checksum=None,
            last_reconciled_at=None,
            legacy_source_key=None,
            legacy_source_signature=None,
        )
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


def reverse(apps, schema_editor):
    _fail("reverse_refused")


class Migration(migrations.Migration):
    dependencies = [
        ("assets", "0104_asset_type_composition_backfill"),
        ("extras", "0114_asset_type_definition_schema"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [migrations.RunPython(forward, reverse)]
