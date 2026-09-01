from django.db import migrations


class MigrationConflict(RuntimeError):
    pass


def forward(apps, schema_editor):
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
        raise MigrationConflict("issue479:composition_count")


def reverse(apps, schema_editor):
    raise MigrationConflict("issue479:reverse_refused")


class Migration(migrations.Migration):
    dependencies = [("assets", "0103_asset_type_data_backfill")]

    operations = [migrations.RunPython(forward, reverse)]
