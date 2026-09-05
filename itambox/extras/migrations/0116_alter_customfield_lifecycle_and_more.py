from django.db import migrations, models
from django.utils import timezone


def normalize_legacy_deleted_lifecycle(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    for model_name in ("CustomField", "CustomFieldChoice", "CustomFieldChoiceSet", "CustomFieldset"):
        model = apps.get_model("extras", model_name)
        for definition in model._base_manager.using(db_alias).filter(lifecycle="deleted"):
            updates = {"lifecycle": "deprecated"}
            if definition.deleted_at is None:
                updates["deleted_at"] = timezone.now()
            model._base_manager.using(db_alias).filter(pk=definition.pk).update(**updates)


def refuse_reverse(apps, schema_editor):
    raise RuntimeError("extras.0116 lifecycle normalization is intentionally irreversible")


class Migration(migrations.Migration):
    dependencies = [
        ("extras", "0115_asset_type_fieldset_cutover"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.AlterField(
            model_name="customfield",
            name="lifecycle",
            field=models.CharField(
                choices=[("active", "Active"), ("deprecated", "Deprecated")], default="active", max_length=16
            ),
        ),
        migrations.AlterField(
            model_name="customfieldchoice",
            name="lifecycle",
            field=models.CharField(
                choices=[("active", "Active"), ("deprecated", "Deprecated")], default="active", max_length=16
            ),
        ),
        migrations.AlterField(
            model_name="customfieldchoiceset",
            name="lifecycle",
            field=models.CharField(
                choices=[("active", "Active"), ("deprecated", "Deprecated")], default="active", max_length=16
            ),
        ),
        migrations.AlterField(
            model_name="customfieldset",
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
