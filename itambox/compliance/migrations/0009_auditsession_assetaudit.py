# Move AuditSession + AssetAudit from assets to compliance.
#
# The model classes have been moved to compliance/models.py. This migration
# creates the model state in the compliance app and renames the physical
# tables in place (no data touched). The companion assets/0037 removes the
# old state from the assets app. compliance/0010 repoints the ContentType
# rows so every GenericFK keeps resolving.

import core.mixins
import core.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0037_remove_auditsession_created_by_and_more'),
        ('compliance', '0008_alter_assetmaintenance_deleted_at_and_more'),
        ('organization', '0013_assetholder_custom_field_data_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='AuditSession',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('deleted_at', models.DateTimeField(blank=True, db_index=True, editable=False, null=True)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('name', models.CharField(max_length=200)),
                        ('status', models.CharField(choices=[('planned', 'Planned'), ('active', 'Active'), ('completed', 'Completed')], default='planned', max_length=20)),
                        ('started_at', models.DateTimeField(auto_now_add=True)),
                        ('completed_at', models.DateTimeField(blank=True, null=True)),
                        ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='audit_sessions', to=settings.AUTH_USER_MODEL)),
                        ('location', models.ForeignKey(blank=True, help_text='Target location expected to be audited. If omitted, applies globally.', null=True, on_delete=django.db.models.deletion.SET_NULL, to='organization.location')),
                    ],
                    options={
                        'verbose_name': 'Audit Session',
                        'verbose_name_plural': 'Audit Sessions',
                        'ordering': ['-started_at'],
                    },
                    bases=(core.mixins.TaggableMixin, core.mixins.ExportableMixin, core.mixins.CloneableMixin, core.models.ChangeLoggingMixin, models.Model),
                ),
                migrations.CreateModel(
                    name='AssetAudit',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('timestamp', models.DateTimeField(auto_now_add=True)),
                        ('notes', models.TextField(blank=True)),
                        ('verification_method', models.CharField(choices=[('barcode', 'Barcode Scan'), ('rfid', 'RFID Reader'), ('manual', 'Manual Input'), ('auto', 'Agent API Handshake')], default='manual', max_length=30)),
                        ('asset', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='audits', to='assets.asset')),
                        ('auditor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='audits_performed', to=settings.AUTH_USER_MODEL)),
                        ('location', models.ForeignKey(help_text='The observed physical location of the asset during audit.', on_delete=django.db.models.deletion.PROTECT, to='organization.location')),
                        ('status', models.ForeignKey(help_text='The observed physical status of the asset during audit.', on_delete=django.db.models.deletion.PROTECT, to='assets.statuslabel')),
                        ('session', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='audits', to='compliance.auditsession')),
                    ],
                    options={
                        'verbose_name': 'Asset Audit',
                        'verbose_name_plural': 'Asset Audits',
                        'ordering': ['-timestamp'],
                        'constraints': [models.UniqueConstraint(fields=('session', 'asset'), name='unique_session_asset')],
                    },
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    'ALTER TABLE assets_auditsession RENAME TO compliance_auditsession',
                    reverse_sql='ALTER TABLE compliance_auditsession RENAME TO assets_auditsession',
                ),
                migrations.RunSQL(
                    'ALTER TABLE assets_assetaudit RENAME TO compliance_assetaudit',
                    reverse_sql='ALTER TABLE compliance_assetaudit RENAME TO assets_assetaudit',
                ),
            ],
        ),
    ]
