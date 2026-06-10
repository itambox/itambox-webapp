from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('compliance', '0010_repoint_audit_contenttypes'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name='AssetMaintenance'),
            ],
            database_operations=[],
        ),
    ]
