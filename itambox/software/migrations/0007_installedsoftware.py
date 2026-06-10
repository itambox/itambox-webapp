# Move InstalledSoftware from assets to software.
#
# The model class has been moved to software/models.py. This migration
# creates the model state in the software app and renames the physical
# table in place (no data is touched). The companion migration
# assets/0036 removes the old state from the assets app, and
# 0008_repoint_installedsoftware_contenttype repoints the
# django_content_type rows so every GenericFK keeps resolving.

import core.models
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('software', '0006_software_custom_field_data'),
        # The table this migration renames is created by the assets app on fresh DBs.
        ('assets', '0035_supplier_custom_field_data'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='InstalledSoftware',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('version_detected', models.CharField(blank=True, help_text='Specific version discovered on the asset (e.g., 16.78.1)', max_length=100)),
                        ('install_date', models.DateField(blank=True, db_index=True, help_text='Estimated or known installation date', null=True)),
                        ('discovered_by_agent', models.CharField(blank=True, help_text='Identifier for the discovery source or agent (e.g., SCCM, Intune, Lansweeper)', max_length=100, verbose_name='Discovered By')),
                        ('last_seen_date', models.DateTimeField(blank=True, db_index=True, help_text='Timestamp when this software was last detected on the asset', null=True)),
                        ('notes', models.TextField(blank=True, help_text='Optional notes specific to this installation')),
                        ('asset', models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.CASCADE, related_name='installed_software', to='assets.asset')),
                        ('software', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='installed_instances', to='software.software')),
                    ],
                    options={
                        'verbose_name': 'Installed Software Instance',
                        'verbose_name_plural': 'Installed Software Instances',
                        'ordering': ['asset', 'software', '-last_seen_date'],
                        'constraints': [models.UniqueConstraint(fields=('asset', 'software', 'version_detected'), name='unique_asset_software_version')],
                    },
                    bases=(core.models.ChangeLoggingMixin, models.Model),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    'ALTER TABLE assets_installedsoftware RENAME TO software_installedsoftware',
                    reverse_sql='ALTER TABLE software_installedsoftware RENAME TO assets_installedsoftware',
                ),
            ],
        ),
    ]
