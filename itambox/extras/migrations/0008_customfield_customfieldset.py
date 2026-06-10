# Move CustomField/CustomFieldset from assets to extras.
#
# The model classes have lived in extras/models.py for a while but were pinned
# back to the assets app via Meta.app_label/db_table. This migration completes
# the move: the model *state* is created here while the existing tables are
# renamed in place (no data is touched). The companion migration
# assets/0033 removes the old state from the assets app, and 0009 repoints the
# django_content_type rows so every GenericFK (changelog, journal, bookmarks)
# keeps working.

import core.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('extras', '0007_alter_tag_name_alter_tag_slug_and_more'),
        # The tables this migration renames are created by the assets app on
        # fresh databases.
        ('assets', '0032_remove_assettype_unique_manufacturer_model_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='CustomField',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('deleted_at', models.DateTimeField(blank=True, db_index=True, editable=False, null=True)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('name', models.SlugField(help_text='Slug-like name (e.g. sim_card_number)', verbose_name='Field Name')),
                        ('label', models.CharField(db_index=True, max_length=100, verbose_name='Display Label')),
                        ('field_type', models.CharField(choices=[('text', 'Text'), ('number', 'Number'), ('date', 'Date'), ('boolean', 'Boolean'), ('select', 'Select / Dropdown')], db_index=True, default='text', max_length=50, verbose_name='Field Type')),
                        ('choices', models.TextField(blank=True, help_text="New-line separated list of choices (only for 'select' type)", null=True)),
                        ('required', models.BooleanField(db_index=True, default=False, verbose_name='Required')),
                        ('model_level', models.BooleanField(db_index=True, default=False, help_text='If True, this field defines a hardware specification on the Asset Type (e.g. CPU, RAM) rather than a device-specific instance detail (e.g. Hostname, OS version).', verbose_name='Model Level / Specification')),
                    ],
                    options={
                        'verbose_name': 'Custom Field',
                        'verbose_name_plural': 'Custom Fields',
                        'ordering': ['label'],
                        'constraints': [models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('name',), name='unique_customfield_name_active')],
                    },
                    bases=(core.models.ChangeLoggingMixin, models.Model),
                ),
                migrations.CreateModel(
                    name='CustomFieldset',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('deleted_at', models.DateTimeField(blank=True, db_index=True, editable=False, null=True)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('name', models.CharField(max_length=100, verbose_name='Fieldset Name')),
                        ('fields', models.ManyToManyField(blank=True, related_name='fieldsets', to='extras.customfield', verbose_name='Custom Fields')),
                    ],
                    options={
                        'verbose_name': 'Custom Fieldset',
                        'verbose_name_plural': 'Custom Fieldsets',
                        'ordering': ['name'],
                        'constraints': [models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('name',), name='unique_customfieldset_name_active')],
                    },
                    bases=(core.models.ChangeLoggingMixin, models.Model),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    'ALTER TABLE assets_customfield RENAME TO extras_customfield',
                    reverse_sql='ALTER TABLE extras_customfield RENAME TO assets_customfield',
                ),
                migrations.RunSQL(
                    'ALTER TABLE assets_customfieldset RENAME TO extras_customfieldset',
                    reverse_sql='ALTER TABLE extras_customfieldset RENAME TO assets_customfieldset',
                ),
                migrations.RunSQL(
                    'ALTER TABLE assets_customfieldset_fields RENAME TO extras_customfieldset_fields',
                    reverse_sql='ALTER TABLE extras_customfieldset_fields RENAME TO assets_customfieldset_fields',
                ),
            ],
        ),
    ]
