from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("extras", "0100_issue88_shard_39_extras_relations"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "DROP INDEX IF EXISTS "
                "core_webhookendpoint_name_9c6e0239_like"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
