from decimal import Decimal

from django.db import migrations

from ._core_vocabulary import CHOICE_SETS, FIELDS, FIELDSETS


class MigrationConflict(RuntimeError):
    pass


def _fail(code):
    raise MigrationConflict(f"issue479:{code}")


def _field_options(row, choice_set_id):
    return {
        "namespace": "itambox",
        "label": row["label"],
        "help_text": row["label"],
        "field_type": row["field_type"],
        "scope": row["scope"],
        "quantity_kind": row.get("quantity_kind"),
        "canonical_unit": row.get("canonical_unit"),
        "minimum_value": Decimal(row["minimum"]) if row.get("minimum") is not None else None,
        "maximum_value": Decimal(row["maximum"]) if row.get("maximum") is not None else None,
        "regex": row.get("regex"),
        "decimal_scale": row.get("decimal_scale"),
        "max_values": row.get("max_values"),
        "text_max_length": row.get("text_max_length"),
        "validation_rule": row.get("validation_rule"),
        "required": False,
        "nullable": False,
        "mappings": [],
        "choice_set_id": choice_set_id,
        "management_kind": "core",
        "version": 1,
        "lifecycle": "active",
        "deprecated_at": None,
        "replaced_by": None,
        "managed_paths": {},
        "source_checksum": None,
        "last_reconciled_at": None,
    }


def _content_type_ids(apps, db_alias, scope):
    ContentType = apps.get_model("contenttypes", "ContentType")
    models = {
        "asset_type": ["assettype"],
        "asset": ["asset"],
        "both": ["asset", "assettype"],
    }[scope]
    content_type_manager = ContentType._base_manager.using(db_alias)
    return [content_type_manager.get_or_create(app_label="assets", model=model)[0].pk for model in models]


def _get_core_fieldset(CustomFieldset, db_alias, slug, label):
    existing = list(CustomFieldset._base_manager.using(db_alias).filter(namespace="itambox", slug=slug))
    if len(existing) > 1 or existing and (existing[0].deleted_at is not None or existing[0].management_kind != "core"):
        _fail("core_fieldset_identity_collision")
    if existing:
        return existing[0]
    return CustomFieldset._base_manager.using(db_alias).create(
        namespace="itambox",
        slug=slug,
        name=label,
        label=label,
        description=f"Normative {label.lower()} specification section.",
        management_kind="core",
        version=1,
        lifecycle="active",
    )


def forward(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    CustomField = apps.get_model("extras", "CustomField")
    CustomFieldChoiceSet = apps.get_model("extras", "CustomFieldChoiceSet")
    CustomFieldChoice = apps.get_model("extras", "CustomFieldChoice")
    CustomFieldset = apps.get_model("extras", "CustomFieldset")
    CustomFieldsetField = apps.get_model("extras", "CustomFieldsetField")
    field_object_types = CustomField.object_types.through

    choice_sets = {}
    for slug, (label, choices) in CHOICE_SETS.items():
        existing_choice_sets = list(
            CustomFieldChoiceSet._base_manager.using(db_alias).filter(namespace="itambox", slug=slug)
        )
        if len(existing_choice_sets) > 1:
            _fail("core_choice_set_collision")
        if existing_choice_sets:
            choice_set = existing_choice_sets[0]
            if choice_set.deleted_at is not None or choice_set.management_kind != "core":
                _fail("core_choice_set_collision")
        else:
            choice_set = CustomFieldChoiceSet._base_manager.using(db_alias).create(
                namespace="itambox",
                slug=slug,
                label=label,
                management_kind="core",
                version=1,
                lifecycle="active",
            )
        CustomFieldChoiceSet._base_manager.using(db_alias).filter(pk=choice_set.pk).update(
            label=label,
            management_kind="core",
            version=1,
            lifecycle="active",
            deprecated_at=None,
            replaced_by=None,
            managed_paths={},
            source_checksum=None,
            last_reconciled_at=None,
        )
        choice_sets[slug] = choice_set
        existing_choice_rows = list(
            CustomFieldChoice._base_manager.using(db_alias)
            .filter(choice_set_id=choice_set.pk)
            .values("key", "deleted_at", "management_kind")
        )
        existing_keys = {row["key"] for row in existing_choice_rows}
        desired_keys = {key for key, _ in choices}
        if existing_keys - desired_keys or any(
            row["deleted_at"] is not None or row["management_kind"] != "core" for row in existing_choice_rows
        ):
            _fail("core_choice_set_collision")
        for index, (key, choice_label) in enumerate(choices, start=1):
            CustomFieldChoice._base_manager.using(db_alias).update_or_create(
                choice_set_id=choice_set.pk,
                key=key,
                defaults={
                    "label": choice_label,
                    "position": index * 10,
                    "management_kind": "core",
                    "version": 1,
                    "lifecycle": "active",
                    "deprecated_at": None,
                    "replaced_by": None,
                    "managed_paths": {},
                    "source_checksum": None,
                    "deleted_at": None,
                },
            )

    fields_by_name = {}
    for row in FIELDS:
        choice_set_id = choice_sets[row["choice_set"]].pk if row.get("choice_set") else None
        options = _field_options(row, choice_set_id)
        existing = list(CustomField._base_manager.using(db_alias).filter(name=row["key"]).order_by("pk"))
        if (
            len(existing) > 1
            or existing
            and (
                existing[0].deleted_at is not None
                or existing[0].namespace != "itambox"
                or existing[0].management_kind != "core"
            )
        ):
            _fail("core_field_identity_collision")
        if existing:
            field = existing[0]
            CustomField._base_manager.using(db_alias).filter(pk=field.pk).update(**options)
        else:
            field = CustomField._base_manager.using(db_alias).create(
                name=row["key"],
                choices="",
                **options,
            )
        fields_by_name[row["key"]] = field
        field_object_types._base_manager.using(db_alias).filter(customfield_id=field.pk).delete()
        ids = _content_type_ids(apps, db_alias, row["scope"])
        field_object_types._base_manager.using(db_alias).bulk_create(
            [field_object_types(customfield_id=field.pk, contenttype_id=content_type_id) for content_type_id in ids]
        )

    fieldsets_by_slug = {}
    for slug, label in FIELDSETS.items():
        fieldset = _get_core_fieldset(CustomFieldset, db_alias, slug, label)
        CustomFieldset._base_manager.using(db_alias).filter(pk=fieldset.pk).update(
            name=label,
            label=label,
            description=f"Normative {label.lower()} specification section.",
            management_kind="core",
            version=1,
            lifecycle="active",
            deprecated_at=None,
            replaced_by=None,
            managed_paths={},
            source_checksum=None,
            last_reconciled_at=None,
        )
        fieldsets_by_slug[slug] = fieldset
        CustomFieldsetField._base_manager.using(db_alias).filter(fieldset_id=fieldset.pk).delete()

    for row in FIELDS:
        CustomFieldsetField._base_manager.using(db_alias).create(
            fieldset_id=fieldsets_by_slug[row["fieldset"]].pk,
            custom_field_id=fields_by_name[row["key"]].pk,
            position=row["position"],
        )


def reverse(apps, schema_editor):
    _fail("reverse_refused")


class Migration(migrations.Migration):
    dependencies = [
        ("assets", "0105_asset_type_core_adoption"),
        ("extras", "0114_asset_type_definition_schema"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [migrations.RunPython(forward, reverse)]
