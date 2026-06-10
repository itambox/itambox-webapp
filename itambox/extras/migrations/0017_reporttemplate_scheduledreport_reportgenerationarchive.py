import core.models
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('core', '0025_remove_journalentry_bookmark_attachments'),
        ('django_q', '0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more'),
        ('extras', '0016_repoint_group3_contenttypes'),
        ('organization', '0013_assetholder_custom_field_data_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='ReportTemplate',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('deleted_at', models.DateTimeField(blank=True, null=True)),
                        ('name', models.CharField(max_length=255)),
                        ('description', models.TextField(blank=True)),
                        ('report_type', models.CharField(choices=[
                            ('asset_summary', 'Asset Inventory Summary'),
                            ('license_utilization', 'License Utilization'),
                            ('subscription_renewals', 'Subscription Renewals'),
                            ('asset_maintenance', 'Asset Maintenance & Repairs'),
                            ('asset_depreciation', 'Asset Depreciation Summary'),
                            ('software_inventory', 'Software Catalog & Installations'),
                        ], max_length=50)),
                        ('included_columns', models.JSONField(blank=True, default=list)),
                        ('include_summary_cards', models.BooleanField(default=True)),
                        ('include_distribution_chart', models.BooleanField(default=False)),
                        ('group_by_field', models.CharField(blank=True, max_length=100, null=True)),
                        ('style_preset', models.CharField(default='default', max_length=50)),
                        ('advanced_mode', models.BooleanField(default=False)),
                        ('template_content', models.TextField(blank=True)),
                        ('tenant', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='report_templates', to='organization.tenant')),
                        ('filter_tenants', models.ManyToManyField(blank=True, related_name='filtered_templates', to='organization.tenant')),
                    ],
                    options={
                        'verbose_name': 'Report Template',
                        'verbose_name_plural': 'Report Templates',
                        'ordering': ['name'],
                    },
                    bases=(core.models.ChangeLoggingMixin, models.Model),
                ),
                migrations.AddConstraint(
                    model_name='reporttemplate',
                    constraint=models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('name',), name='unique_reporttemplate_name_active'),
                ),
                migrations.CreateModel(
                    name='ScheduledReport',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('name', models.CharField(max_length=255)),
                        ('recipients', models.TextField(blank=True, default='')),
                        ('frequency', models.CharField(choices=[
                            ('once', 'Once'), ('hourly', 'Hourly'), ('daily', 'Daily'), ('weekly', 'Weekly'),
                            ('biweekly', 'Biweekly'), ('monthly', 'Monthly'), ('quarterly', 'Quarterly'),
                            ('yearly', 'Yearly'), ('cron', 'Custom Cron Expression'),
                        ], default='weekly', max_length=50)),
                        ('format', models.CharField(choices=[('html', 'HTML Email'), ('csv', 'CSV Attachment')], default='html', max_length=20)),
                        ('cron_expression', models.CharField(blank=True, max_length=100, null=True)),
                        ('start_time', models.TimeField(blank=True, null=True)),
                        ('save_to_archive', models.BooleanField(default=True)),
                        ('is_active', models.BooleanField(default=True)),
                        ('last_run', models.DateTimeField(blank=True, null=True)),
                        ('last_status', models.CharField(blank=True, max_length=50, null=True)),
                        ('report', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='schedules', to='extras.reporttemplate')),
                        ('schedule', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='scheduled_reports', to='django_q.schedule')),
                        ('tenant', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='scheduled_reports', to='organization.tenant')),
                        ('filter_tenants', models.ManyToManyField(blank=True, related_name='filtered_schedules', to='organization.tenant')),
                        ('channels', models.ManyToManyField(blank=True, related_name='scheduled_reports', to='core.notificationchannel')),
                    ],
                    options={
                        'verbose_name': 'Scheduled Report',
                        'verbose_name_plural': 'Scheduled Reports',
                        'ordering': ['name'],
                    },
                    bases=(core.models.ChangeLoggingMixin, models.Model),
                ),
                migrations.CreateModel(
                    name='ReportGenerationArchive',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('generated_at', models.DateTimeField(auto_now_add=True)),
                        ('format', models.CharField(max_length=20)),
                        ('status', models.CharField(max_length=50)),
                        ('error_message', models.TextField(blank=True, null=True)),
                        ('file', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='report_archives', to='extras.fileattachment')),
                        ('scheduled_report', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='archives', to='extras.scheduledreport')),
                        ('tenant', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='report_archives', to='organization.tenant')),
                    ],
                    options={
                        'verbose_name': 'Report Generation Archive',
                        'verbose_name_plural': 'Report Generation Archives',
                        'ordering': ['-generated_at'],
                    },
                    bases=(core.models.ChangeLoggingMixin, models.Model),
                ),
            ],
            database_operations=[
                # Main tables
                migrations.RunSQL(
                    'ALTER TABLE core_reporttemplate RENAME TO extras_reporttemplate',
                    reverse_sql='ALTER TABLE extras_reporttemplate RENAME TO core_reporttemplate',
                ),
                migrations.RunSQL(
                    'ALTER TABLE core_scheduledreport RENAME TO extras_scheduledreport',
                    reverse_sql='ALTER TABLE extras_scheduledreport RENAME TO core_scheduledreport',
                ),
                migrations.RunSQL(
                    'ALTER TABLE core_reportgenerationarchive RENAME TO extras_reportgenerationarchive',
                    reverse_sql='ALTER TABLE extras_reportgenerationarchive RENAME TO core_reportgenerationarchive',
                ),
                # M2M tables
                migrations.RunSQL(
                    'ALTER TABLE core_reporttemplate_filter_tenants RENAME TO extras_reporttemplate_filter_tenants',
                    reverse_sql='ALTER TABLE extras_reporttemplate_filter_tenants RENAME TO core_reporttemplate_filter_tenants',
                ),
                migrations.RunSQL(
                    'ALTER TABLE core_scheduledreport_filter_tenants RENAME TO extras_scheduledreport_filter_tenants',
                    reverse_sql='ALTER TABLE extras_scheduledreport_filter_tenants RENAME TO core_scheduledreport_filter_tenants',
                ),
                migrations.RunSQL(
                    'ALTER TABLE core_scheduledreport_channels RENAME TO extras_scheduledreport_channels',
                    reverse_sql='ALTER TABLE extras_scheduledreport_channels RENAME TO core_scheduledreport_channels',
                ),
            ],
        ),
    ]
