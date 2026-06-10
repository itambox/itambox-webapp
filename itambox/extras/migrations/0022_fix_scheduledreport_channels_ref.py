from django.db import migrations, models


class Migration(migrations.Migration):
    """State-only: update ScheduledReport.channels FK target from
    core.notificationchannel to extras.notificationchannel after E5 move."""

    dependencies = [
        ('extras', '0020_notificationchannel_alertrule_alertlog'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name='scheduledreport',
                    name='channels',
                    field=models.ManyToManyField(blank=True, related_name='scheduled_reports', to='extras.notificationchannel'),
                ),
            ],
            database_operations=[],
        ),
    ]
