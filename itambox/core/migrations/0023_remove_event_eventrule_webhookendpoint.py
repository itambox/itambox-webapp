from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0022_alertlog_severity_index_and_channel_choices'),
        ('extras', '0011_event_eventrule_webhookendpoint'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name='Event'),
                migrations.DeleteModel(name='EventRule'),
                migrations.DeleteModel(name='WebhookEndpoint'),
            ],
            database_operations=[],
        ),
    ]
