from django.db import migrations, models
from django.utils import timezone


def normalize_legacy_deleted_lifecycle(apps, schema_editor):
    model = apps.get_model("assets", "AssetType")
    db_alias = schema_editor.connection.alias
    for definition in model._base_manager.using(db_alias).filter(lifecycle="deleted"):
        updates = {"lifecycle": "deprecated"}
        if definition.deleted_at is None:
            updates["deleted_at"] = timezone.now()
        model._base_manager.using(db_alias).filter(pk=definition.pk).update(**updates)


def refuse_reverse(apps, schema_editor):
    raise RuntimeError("assets.0109 lifecycle normalization is intentionally irreversible")


class Migration(migrations.Migration):
    dependencies = [
        ("assets", "0108_asset_type_singular_cutover"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.AlterField(
            model_name="assettype",
            name="lifecycle",
            field=models.CharField(
                choices=[("active", "Active"), ("deprecated", "Deprecated")], default="active", max_length=16
            ),
        ),
        migrations.RunPython(
            normalize_legacy_deleted_lifecycle,
            reverse_code=refuse_reverse,
        ),
    ]
