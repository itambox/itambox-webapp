from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0018_alter_alertrule_name_alter_notificationchannel_name_and_more'),
    ]

    operations = [
        # Add warranty_expiry to alert_type choices (field length unchanged, just choice added)
        migrations.AlterField(
            model_name='alertrule',
            name='alert_type',
            field=models.CharField(
                max_length=50,
                choices=[
                    ('low_stock', 'Low Stock Alert'),
                    ('upcoming_eol', 'Upcoming EOL Planning'),
                    ('license_expiry', 'License Expiry Alert'),
                    ('renewal_due', 'Renewal Due Alert'),
                    ('warranty_expiry', 'Warranty Expiry Alert'),
                ],
            ),
        ),
        # Remove recipients from AlertRule
        migrations.RemoveField(
            model_name='alertrule',
            name='recipients',
        ),
        # Add severity to AlertLog
        migrations.AddField(
            model_name='alertlog',
            name='severity',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('info', 'Info'),
                    ('warning', 'Warning'),
                    ('critical', 'Critical'),
                ],
                default='warning',
                db_index=True,
            ),
        ),
        # Add delivery_status to AlertLog
        migrations.AddField(
            model_name='alertlog',
            name='delivery_status',
            field=models.JSONField(
                default=dict,
                blank=True,
                help_text="Per-channel delivery result: {channel_pk: 'ok'|'failed'|'error: ...'}",
            ),
        ),
        # Add severity index
        migrations.AddIndex(
            model_name='alertlog',
            index=models.Index(fields=['severity'], name='core_alertlog_severity_idx'),
        ),
    ]
