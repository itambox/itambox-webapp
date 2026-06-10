import core.mixins
import core.models
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0037_remove_auditsession_created_by_and_more'),
        ('compliance', '0011_remove_assetmaintenance'),
        ('extras', '0010_customfield_object_types'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='AssetMaintenance',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('deleted_at', models.DateTimeField(blank=True, db_index=True, editable=False, null=True)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('title', models.CharField(default='Maintenance', max_length=200)),
                        ('description', models.TextField(blank=True)),
                        ('performed_by', models.CharField(blank=True, max_length=200)),
                        ('maintenance_type', models.CharField(choices=[('upgrade', 'Upgrade'), ('repair', 'Repair'), ('calibration', 'Calibration'), ('software_support', 'Software Support'), ('hardware_support', 'Hardware Support')], db_index=True, default='repair', max_length=50, verbose_name='Maintenance Type')),
                        ('status', models.CharField(choices=[('scheduled', 'Scheduled'), ('in_progress', 'In Progress'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], db_index=True, default='scheduled', max_length=20)),
                        ('cost', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, verbose_name='Maintenance Cost')),
                        ('start_date', models.DateField(db_index=True, verbose_name='Start Date')),
                        ('completion_date', models.DateField(blank=True, db_index=True, null=True, verbose_name='Completion Date')),
                        ('notes', models.TextField(blank=True)),
                        ('asset', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='maintenances', to='assets.asset')),
                        ('supplier', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='assets.supplier', verbose_name='Supplier/Vendor')),
                        ('tags', models.ManyToManyField(blank=True, related_name='asset_maintenances', to='extras.tag')),
                    ],
                    options={
                        'verbose_name': 'Asset Maintenance',
                        'verbose_name_plural': 'Asset Maintenances',
                        'ordering': ['-start_date'],
                    },
                    bases=(core.mixins.TaggableMixin, core.mixins.CloneableMixin, core.mixins.ExportableMixin, core.models.ChangeLoggingMixin, models.Model),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    'ALTER TABLE compliance_assetmaintenance RENAME TO assets_assetmaintenance',
                    reverse_sql='ALTER TABLE assets_assetmaintenance RENAME TO compliance_assetmaintenance',
                ),
                migrations.RunSQL(
                    'ALTER TABLE compliance_assetmaintenance_tags RENAME TO assets_assetmaintenance_tags',
                    reverse_sql='ALTER TABLE assets_assetmaintenance_tags RENAME TO compliance_assetmaintenance_tags',
                ),
            ],
        ),
    ]
