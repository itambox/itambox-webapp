import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('extras', '0028_encrypt_webhookendpoint_secret'),
    ]

    operations = [
        migrations.AddField(
            model_name='eventrule',
            name='webhook',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='event_rules',
                to='extras.webhookendpoint',
                help_text=(
                    "Endpoint to call when the action type is Webhook. "
                    "Takes precedence over any 'url' in action_config."
                ),
            ),
        ),
    ]
