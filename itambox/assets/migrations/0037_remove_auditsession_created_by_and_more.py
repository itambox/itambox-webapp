# Move AuditSession + AssetAudit from assets to compliance.
#
# The model classes have been moved to compliance/models.py. This migration
# removes the model state from the assets app. The companion compliance/0009
# creates the model state there. No DB operations here — the table rename
# happens in compliance/0009 database_operations.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0036_remove_installedsoftware'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name='auditsession',
                    name='created_by',
                ),
                migrations.RemoveField(
                    model_name='auditsession',
                    name='location',
                ),
                migrations.DeleteModel(
                    name='AssetAudit',
                ),
                migrations.DeleteModel(
                    name='AuditSession',
                ),
            ],
            database_operations=[],
        ),
    ]
