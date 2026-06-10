# Standardize custom field storage on the NetBox convention: the JSON column
# is custom_field_data everywhere (provided by core.mixins.CustomFieldDataMixin).
# RenameField preserves the data; hand-written because makemigrations cannot
# detect renames non-interactively.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0033_remove_customfieldset_fields_and_more'),
    ]

    operations = [
        migrations.RenameField(
            model_name='asset',
            old_name='custom_values',
            new_name='custom_field_data',
        ),
        migrations.RenameField(
            model_name='assettype',
            old_name='custom_values',
            new_name='custom_field_data',
        ),
        migrations.AlterField(
            model_name='asset',
            name='custom_field_data',
            field=models.JSONField(blank=True, default=dict, verbose_name='Custom Field Data'),
        ),
        migrations.AlterField(
            model_name='assettype',
            name='custom_field_data',
            field=models.JSONField(blank=True, default=dict, verbose_name='Custom Field Data'),
        ),
    ]
