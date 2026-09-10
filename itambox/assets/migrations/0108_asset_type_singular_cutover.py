from django.db import migrations


class MigrationConflict(RuntimeError):
    pass


def reverse_refused(apps, schema_editor):
    raise MigrationConflict("issue479:reverse_refused")


class Migration(migrations.Migration):
    dependencies = [
        ("assets", "0107_asset_type_library_contract"),
        ("extras", "0115_asset_type_fieldset_cutover"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="assettype",
            name="custom_fieldset",
        ),
        migrations.RunPython(migrations.RunPython.noop, reverse_code=reverse_refused),
    ]
