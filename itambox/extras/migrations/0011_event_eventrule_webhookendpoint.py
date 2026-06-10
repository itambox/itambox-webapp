import core.models
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('core', '0022_alertlog_severity_index_and_channel_choices'),
        ('extras', '0010_customfield_object_types'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='WebhookEndpoint',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('name', models.CharField(max_length=255, unique=True)),
                        ('url', models.URLField(max_length=2000)),
                        ('http_method', models.CharField(choices=[('GET', 'GET'), ('POST', 'POST'), ('PUT', 'PUT'), ('PATCH', 'PATCH')], default='POST', max_length=10)),
                        ('headers', models.JSONField(blank=True, default=dict)),
                        ('secret', models.CharField(blank=True, help_text='Shared secret for HMAC payload signing', max_length=255)),
                        ('enabled', models.BooleanField(default=True)),
                        ('retry_count', models.PositiveSmallIntegerField(default=3, help_text='Max retry attempts on failure')),
                        ('retry_backoff', models.PositiveSmallIntegerField(default=60, help_text='Backoff in seconds between retries')),
                    ],
                    options={
                        'verbose_name': 'Webhook Endpoint',
                        'verbose_name_plural': 'Webhook Endpoints',
                        'ordering': ['name'],
                    },
                    bases=(core.models.ChangeLoggingMixin, models.Model),
                ),
                migrations.CreateModel(
                    name='EventRule',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('name', models.CharField(max_length=255)),
                        ('events', models.JSONField(default=list, help_text="List of event action types, e.g. ['create', 'update']")),
                        ('conditions', models.JSONField(blank=True, default=dict, help_text='Optional conditions for rule matching')),
                        ('action_type', models.CharField(choices=[('webhook', 'Webhook'), ('notification', 'Notification'), ('script', 'Script')], max_length=20)),
                        ('action_config', models.JSONField(blank=True, default=dict, help_text='Configuration for the action (webhook URL, template, etc.)')),
                        ('enabled', models.BooleanField(default=True)),
                        ('model', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='event_rules', to='contenttypes.contenttype')),
                    ],
                    options={
                        'verbose_name': 'Event Rule',
                        'verbose_name_plural': 'Event Rules',
                        'ordering': ['name'],
                    },
                    bases=(core.models.ChangeLoggingMixin, models.Model),
                ),
                migrations.CreateModel(
                    name='Event',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('object_id', models.PositiveBigIntegerField(db_index=True)),
                        ('action', models.CharField(choices=[('create', 'Create'), ('update', 'Update'), ('delete', 'Delete')], db_index=True, max_length=20)),
                        ('timestamp', models.DateTimeField(auto_now_add=True, db_index=True)),
                        ('data', models.JSONField(blank=True, default=dict)),
                        ('processed', models.BooleanField(db_index=True, default=False)),
                        ('model', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='contenttypes.contenttype')),
                    ],
                    options={
                        'verbose_name': 'Event',
                        'verbose_name_plural': 'Events',
                        'ordering': ['-timestamp'],
                        'indexes': [
                            models.Index(fields=['model', 'object_id'], name='core_event_model_i_6d722d_idx'),
                            models.Index(fields=['processed', 'timestamp'], name='core_event_process_17ef77_idx'),
                        ],
                    },
                    bases=(core.models.ChangeLoggingMixin, models.Model),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    'ALTER TABLE core_webhookendpoint RENAME TO extras_webhookendpoint',
                    reverse_sql='ALTER TABLE extras_webhookendpoint RENAME TO core_webhookendpoint',
                ),
                migrations.RunSQL(
                    'ALTER TABLE core_eventrule RENAME TO extras_eventrule',
                    reverse_sql='ALTER TABLE extras_eventrule RENAME TO core_eventrule',
                ),
                migrations.RunSQL(
                    'ALTER TABLE core_event RENAME TO extras_event',
                    reverse_sql='ALTER TABLE extras_event RENAME TO core_event',
                ),
            ],
        ),
    ]
