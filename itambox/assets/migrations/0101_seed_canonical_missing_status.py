from django.db import migrations


def seed_canonical_missing_status(apps, schema_editor):
    status_label = apps.get_model("assets", "StatusLabel")
    status_label.objects.get_or_create(
        slug="missing",
        defaults={"name": "Missing", "type": "undeployable", "color": "dc3545"},
    )


class Migration(migrations.Migration):
    dependencies = [
        ("assets", "0100_issue88_shard_43_assets_seed"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.RunPython(seed_canonical_missing_status, migrations.RunPython.noop),
    ]
