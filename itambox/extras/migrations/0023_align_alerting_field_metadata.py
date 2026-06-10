from django.db import migrations, models


class Migration(migrations.Migration):
    """State-only: deleted_at on alertrule/notificationchannel is missing
    editable=False and db_index=True from the SoftDeleteMixin definition.
    The DB column and index already exist; no schema changes needed."""

    dependencies = [
        ('extras', '0021_repoint_alerting_contenttypes'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name='alertrule',
                    name='deleted_at',
                    field=models.DateTimeField(blank=True, db_index=True, editable=False, null=True),
                ),
                migrations.AlterField(
                    model_name='notificationchannel',
                    name='deleted_at',
                    field=models.DateTimeField(blank=True, db_index=True, editable=False, null=True),
                ),
            ],
            database_operations=[],
        ),
    ]
