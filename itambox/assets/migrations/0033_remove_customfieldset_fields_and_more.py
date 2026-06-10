# State-only companion to extras/0008: remove CustomField/CustomFieldset from
# the assets app state. The database is untouched — extras/0008 already renamed
# the tables, and the AssetType FK keeps pointing at the same (renamed) table.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0032_remove_assettype_unique_manufacturer_model_and_more'),
        ('extras', '0008_customfield_customfieldset'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name='customfieldset',
                    name='fields',
                ),
                migrations.AlterField(
                    model_name='assettype',
                    name='custom_fieldset',
                    field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='asset_types', to='extras.customfieldset', verbose_name='Custom Fieldset'),
                ),
                migrations.DeleteModel(
                    name='CustomField',
                ),
                migrations.DeleteModel(
                    name='CustomFieldset',
                ),
            ],
            database_operations=[],
        ),
    ]
