"""Normalized #479 definition conversion (extras side).

Normalizes the legacy reusable-definition state for the final schema:

* validates object-type applicability and lifecycle/identity invariants;
* backfills ``CustomField.activation`` (composed when the field is a member of
  at least one fieldset, global otherwise) and normalizes soft-deleted rows to
  the final ``deprecated`` lifecycle;
* renumbers definition member positions to their final contiguous ordering and
  forces the deferred position constraints to validate inside this migration.

The migration is the supported upgrade path for the pre-#479 baseline states;
fresh installs skip every step.
"""

from collections import defaultdict

from django.db import migrations, models
from django.db.models import Count
from django.utils import timezone


class MigrationConflict(RuntimeError):
    """Raised when predecessor data cannot be migrated without guessing."""


def _fail(code, detail=None):
    suffix = f":{detail}" if detail else ""
    raise MigrationConflict(f"issue479:{code}{suffix}")


def _check_identity_duplicates(model, fields, db_alias):
    duplicate_groups = (
        model._base_manager.using(db_alias)
        .values(*fields)
        .annotate(identity_count=Count("pk"))
        .filter(identity_count__gt=1)
    )
    first = duplicate_groups.order_by(*fields).first()
    if first is not None:
        _fail("ambiguous_identity", f"{model._meta.db_table}:{fields}:{first}")


def _check_position_rows(model, owner_field, member_field, max_position, db_alias):
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
            if not isinstance(position, int) or not 1 <= position <= max_position:
                _fail("invalid_position", f"{model._meta.db_table}:{row['pk']}:{position}")
            if member_id in seen_members:
                _fail("duplicate_member", f"{model._meta.db_table}:{owner_id}:{member_id}")
            if position in seen_positions:
                _fail("duplicate_position", f"{model._meta.db_table}:{owner_id}:{position}")
            seen_members.add(member_id)
            seen_positions.add(position)


def _preflight_applicability(apps, db_alias):
    CustomField = apps.get_model("extras", "CustomField")
    ContentType = apps.get_model("contenttypes", "ContentType")
    through = CustomField.object_types.through
    object_type_rows = defaultdict(list)
    for field_id, content_type_id in through._base_manager.using(db_alias).values_list(
        "customfield_id", "contenttype_id"
    ):
        object_type_rows[field_id].append(content_type_id)

    content_types = {
        content_type.pk: content_type
        for content_type in ContentType._base_manager.using(db_alias).filter(
            pk__in={content_type_id for ids in object_type_rows.values() for content_type_id in ids}
        )
    }
    for field in CustomField._base_manager.using(db_alias).order_by("pk"):
        content_type_ids = object_type_rows.get(field.pk, [])
        if not content_type_ids:
            _fail("empty_object_types", field.pk)
        for content_type_id in content_type_ids:
            content_type = content_types.get(content_type_id)
            if content_type is None:
                _fail("missing_content_type", f"{field.pk}:{content_type_id}")
            identity = (content_type.app_label, content_type.model)
            try:
                target_model = apps.get_model(*identity)
            except LookupError:
                target_model = None
            if target_model is None:
                _fail("unresolvable_object_type", f"{field.pk}:{content_type.app_label}.{content_type.model}")
            if not any(item.name == "custom_field_data" for item in target_model._meta.concrete_fields):
                _fail("missing_custom_field_data", f"{field.pk}:{content_type.app_label}.{content_type.model}")


def _preflight_lifecycle_and_identity(apps, db_alias):
    definition_models = (
        apps.get_model("extras", "CustomField"),
        apps.get_model("extras", "CustomFieldset"),
        apps.get_model("extras", "CustomFieldChoiceSet"),
        apps.get_model("extras", "CustomFieldChoice"),
    )
    identity_fields = {
        "CustomField": ("name",),
        "CustomFieldset": ("namespace", "slug"),
        "CustomFieldChoiceSet": ("namespace", "slug"),
        "CustomFieldChoice": ("choice_set_id", "key"),
    }
    for model in definition_models:
        _check_identity_duplicates(model, identity_fields[model.__name__], db_alias)
        for definition in model._base_manager.using(db_alias).all().iterator():
            if definition.lifecycle not in {"active", "deprecated", "deleted"}:
                _fail("invalid_lifecycle", f"{model.__name__}:{definition.pk}:{definition.lifecycle}")
            if definition.__class__.__name__ == "CustomFieldset" and (
                not definition.namespace or not definition.slug
            ):
                _fail("missing_fieldset_identity", definition.pk)
            if definition.lifecycle == "active" and definition.deprecated_at is not None:
                _fail("active_with_deprecated_at", f"{model.__name__}:{definition.pk}")
            legacy_deleted_at = getattr(definition, "deleted_at", None)
            if legacy_deleted_at is not None and definition.deprecated_at is not None:
                if legacy_deleted_at != definition.deprecated_at:
                    _fail("ambiguous_deprecation_timestamp", f"{model.__name__}:{definition.pk}")


def _preflight(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    _preflight_applicability(apps, db_alias)
    _preflight_lifecycle_and_identity(apps, db_alias)
    _check_position_rows(
        apps.get_model("extras", "CustomFieldsetField"),
        "fieldset_id",
        "custom_field_id",
        1_000_000,
        db_alias,
    )
    _check_position_rows(
        apps.get_model("extras", "CustomFieldChoice"),
        "choice_set_id",
        "key",
        1_000_000,
        db_alias,
    )


def _backfill(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    CustomField = apps.get_model("extras", "CustomField")
    CustomFieldsetField = apps.get_model("extras", "CustomFieldsetField")
    now = timezone.now()
    activation_updates = []
    for field in CustomField._base_manager.using(db_alias).all().iterator():
        activation = "composed" if CustomFieldsetField._base_manager.using(db_alias).filter(
            custom_field_id=field.pk
        ).exists() else "global"
        activation_updates.append((field.pk, activation))
    for field_id, activation in activation_updates:
        CustomField._base_manager.using(db_alias).filter(pk=field_id).update(activation=activation)

    for model_name in ("CustomField", "CustomFieldChoice", "CustomFieldChoiceSet", "CustomFieldset"):
        model = apps.get_model("extras", model_name)
        for definition in model._base_manager.using(db_alias).all().iterator():
            legacy_deleted_at = getattr(definition, "deleted_at", None)
            if legacy_deleted_at is None and definition.lifecycle != "deleted":
                continue
            deprecated_at = definition.deprecated_at or legacy_deleted_at or now
            model._base_manager.using(db_alias).filter(pk=definition.pk).update(
                lifecycle="deprecated",
                deprecated_at=deprecated_at,
            )


def _make_constraints_immediate(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def _renumber_positions(model, owner_field, member_sort_fields, db_alias):
    owner_ids = model._base_manager.using(db_alias).values_list(owner_field, flat=True).distinct()
    for owner_id in owner_ids:
        rows = list(
            model._base_manager.using(db_alias)
            .filter(**{owner_field: owner_id})
            .order_by("position", *member_sort_fields, "pk")
        )
        for position, row in enumerate(rows, start=1):
            model._base_manager.using(db_alias).filter(pk=row.pk).update(position=position)


def _renumber_definition_positions(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    _renumber_positions(
        apps.get_model("extras", "CustomFieldsetField"),
        "fieldset_id",
        ("custom_field_id",),
        db_alias,
    )
    _renumber_positions(
        apps.get_model("extras", "CustomFieldChoice"),
        "choice_set_id",
        ("key",),
        db_alias,
    )


def refuse_reverse(apps, schema_editor):
    _fail("reverse_refused")


class Migration(migrations.Migration):
    dependencies = [
        ("assets", "0103_asset_type_specification_conversion"),
        ("extras", "0114_asset_type_definition_library_schema"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.RunPython(_preflight, reverse_code=refuse_reverse),
        migrations.RunPython(_backfill, reverse_code=refuse_reverse),
        migrations.RunPython(_renumber_definition_positions, reverse_code=refuse_reverse),
        migrations.RunPython(_make_constraints_immediate, reverse_code=refuse_reverse),
    ]
