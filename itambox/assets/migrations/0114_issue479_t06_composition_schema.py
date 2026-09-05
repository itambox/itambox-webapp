from django.db import migrations, models


class MigrationConflict(RuntimeError):
    """Raised when predecessor composition data cannot be made dense safely."""


def _fail(code, detail=None):
    suffix = f":{detail}" if detail else ""
    raise MigrationConflict(f"issue479:{code}{suffix}")


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


def preflight_composition(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    _preflight_rows(
        apps.get_model("assets", "AssetTypeFieldset"),
        "asset_type_id",
        "fieldset_id",
        db_alias,
    )
    _preflight_rows(
        apps.get_model("assets", "CategoryDefaultFieldset"),
        "category_id",
        "fieldset_id",
        db_alias,
    )


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


def renumber_composition(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    _renumber(
        apps.get_model("assets", "AssetTypeFieldset"),
        "asset_type_id",
        "fieldset_id",
        db_alias,
    )
    _renumber(
        apps.get_model("assets", "CategoryDefaultFieldset"),
        "category_id",
        "fieldset_id",
        db_alias,
    )


def refuse_reverse(apps, schema_editor):
    raise MigrationConflict("issue479:reverse_refused")


class Migration(migrations.Migration):
    dependencies = [
        ("assets", "0113_assettype_library_identity_immutable"),
        ("extras", "0118_issue479_t06_definition_schema"),
    ]

    operations = [
        migrations.RunPython(preflight_composition, reverse_code=refuse_reverse),
        migrations.RemoveConstraint(
            model_name="assettypefieldset",
            name="unique_assettype_fieldset_position",
        ),
        migrations.RemoveConstraint(
            model_name="categorydefaultfieldset",
            name="unique_category_default_position",
        ),
        migrations.RunPython(renumber_composition, reverse_code=refuse_reverse),
        migrations.AddConstraint(
            model_name="assettypefieldset",
            constraint=models.UniqueConstraint(
                deferrable=models.Deferrable.DEFERRED,
                fields=("asset_type", "position"),
                name="unique_assettype_fieldset_position",
            ),
        ),
        migrations.AddConstraint(
            model_name="categorydefaultfieldset",
            constraint=models.UniqueConstraint(
                deferrable=models.Deferrable.DEFERRED,
                fields=("category", "position"),
                name="unique_category_default_position",
            ),
        ),
    ]
