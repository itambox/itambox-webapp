from django.db import migrations


class MigrationConflict(RuntimeError):
    pass


def reverse_refused(apps, schema_editor):
    raise MigrationConflict("issue479:reverse_refused")


class Migration(migrations.Migration):
    dependencies = [
        ("assets", "0106_asset_type_core_seed"),
        ("extras", "0114_asset_type_definition_schema"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="assettype",
            name="unique_manufacturer_model_active",
        ),
    ]
