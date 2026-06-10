# State-only companion to software/0007: remove InstalledSoftware from
# the assets app state. The database is untouched — software/0007 already
# renamed the table.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0035_supplier_custom_field_data'),
        ('software', '0007_installedsoftware'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(
                    name='InstalledSoftware',
                ),
            ],
            database_operations=[],
        ),
    ]
