import core.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0026_remove_reporttemplate_scheduledreport_reportgenerationarchive'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('extras', '0019_align_report_field_metadata'),
        ('organization', '0013_assetholder_custom_field_data_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='NotificationChannel',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('deleted_at', models.DateTimeField(blank=True, null=True)),
                        ('name', models.CharField(max_length=255)),
                        ('channel_type', models.CharField(choices=[('email', 'Email'), ('in_app', 'In-App'), ('slack', 'Slack'), ('teams', 'Microsoft Teams')], max_length=20)),
                        ('enabled', models.BooleanField(default=True)),
                        ('config', models.JSONField(blank=True, default=dict, help_text='Channel-specific config (SMTP settings, webhook URL, etc.)')),
                        ('tenant', models.ForeignKey(blank=True, help_text='The tenant owning this channel. Null represents system-wide channels.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='notification_channels', to='organization.tenant')),
                    ],
                    options={
                        'verbose_name': 'Notification Channel',
                        'verbose_name_plural': 'Notification Channels',
                        'ordering': ['name'],
                    },
                    bases=(core.models.ChangeLoggingMixin, core.models.SoftDeleteMixin, models.Model),
                ),
                migrations.AddConstraint(
                    model_name='notificationchannel',
                    constraint=models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('name',), name='unique_notificationchannel_name_active'),
                ),
                migrations.CreateModel(
                    name='AlertRule',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('deleted_at', models.DateTimeField(blank=True, null=True)),
                        ('name', models.CharField(max_length=255)),
                        ('description', models.TextField(blank=True)),
                        ('alert_type', models.CharField(choices=[('low_stock', 'Low Stock Alert'), ('upcoming_eol', 'Upcoming EOL Planning'), ('license_expiry', 'License Expiry Alert'), ('renewal_due', 'Renewal Due Alert'), ('warranty_expiry', 'Warranty Expiry Alert'), ('audit_overdue', 'Audit Overdue')], max_length=50)),
                        ('threshold_value', models.PositiveIntegerField(help_text='Limit count or days horizon')),
                        ('severity', models.CharField(choices=[('info', 'Info'), ('warning', 'Warning'), ('critical', 'Critical')], default='warning', max_length=20)),
                        ('is_active', models.BooleanField(default=True, help_text='Inactive rules are not evaluated at all.')),
                        ('is_muted', models.BooleanField(default=False, help_text='Muted rules still track alerts in the Alert Center but send no channel notifications.')),
                        ('renotify_interval_days', models.PositiveIntegerField(default=0, help_text='0 = notify once. N = re-send channel notifications every N days while an alert stays unresolved.')),
                        ('last_fired_at', models.DateTimeField(blank=True, editable=False, help_text='When this rule was last evaluated by the engine.', null=True)),
                        ('channels', models.ManyToManyField(blank=True, related_name='alert_rules', to='extras.notificationchannel')),
                        ('tenant', models.ForeignKey(blank=True, help_text='The tenant owning this rule. Null represents system-wide rules.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='alert_rules', to='organization.tenant')),
                    ],
                    options={
                        'verbose_name': 'Alert Rule',
                        'verbose_name_plural': 'Alert Rules',
                        'ordering': ['name'],
                    },
                    bases=(core.models.ChangeLoggingMixin, core.models.SoftDeleteMixin, models.Model),
                ),
                migrations.AddConstraint(
                    model_name='alertrule',
                    constraint=models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('name',), name='unique_alertrule_name_active'),
                ),
                migrations.CreateModel(
                    name='AlertLog',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('subject', models.CharField(max_length=255)),
                        ('message', models.TextField()),
                        ('severity', models.CharField(choices=[('info', 'Info'), ('warning', 'Warning'), ('critical', 'Critical')], db_index=True, default='warning', max_length=20)),
                        ('object_id', models.PositiveBigIntegerField()),
                        ('status', models.CharField(choices=[('active', 'Active'), ('acknowledged', 'Acknowledged'), ('resolved', 'Resolved')], db_index=True, default='active', max_length=20)),
                        ('delivery_status', models.JSONField(blank=True, default=dict, help_text="Per-channel delivery result: {channel_pk: 'ok'|'failed'|'error: ...'}")),
                        ('last_notified_at', models.DateTimeField(blank=True, help_text='When channel notifications were last dispatched for this alert (drives re-notify).', null=True)),
                        ('resolution_notes', models.TextField(blank=True)),
                        ('resolved_at', models.DateTimeField(blank=True, null=True)),
                        ('acknowledged_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='acknowledged_alerts', to=settings.AUTH_USER_MODEL)),
                        ('content_type', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='alert_logs', to='contenttypes.contenttype')),
                        ('resolved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='resolved_alerts', to=settings.AUTH_USER_MODEL)),
                        ('rule', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='logs', to='extras.alertrule')),
                        ('tenant', models.ForeignKey(blank=True, help_text='The tenant owning this log. Null represents system-wide logs.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='alert_logs', to='organization.tenant')),
                    ],
                    options={
                        'verbose_name': 'Alert Log',
                        'verbose_name_plural': 'Alert Logs',
                        'ordering': ['-created_at'],
                        'indexes': [
                            models.Index(fields=['content_type', 'object_id'], name='core_alertl_content_706751_idx'),
                            models.Index(fields=['severity'], name='core_alertl_severit_f0ec11_idx'),
                            models.Index(fields=['status'], name='core_alertl_status_b2f47a_idx'),
                        ],
                    },
                    bases=(models.Model,),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    'ALTER TABLE core_notificationchannel RENAME TO extras_notificationchannel',
                    reverse_sql='ALTER TABLE extras_notificationchannel RENAME TO core_notificationchannel',
                ),
                migrations.RunSQL(
                    'ALTER TABLE core_alertrule RENAME TO extras_alertrule',
                    reverse_sql='ALTER TABLE extras_alertrule RENAME TO core_alertrule',
                ),
                migrations.RunSQL(
                    'ALTER TABLE core_alertrule_channels RENAME TO extras_alertrule_channels',
                    reverse_sql='ALTER TABLE extras_alertrule_channels RENAME TO core_alertrule_channels',
                ),
                migrations.RunSQL(
                    'ALTER TABLE core_alertlog RENAME TO extras_alertlog',
                    reverse_sql='ALTER TABLE extras_alertlog RENAME TO core_alertlog',
                ),
            ],
        ),
    ]
