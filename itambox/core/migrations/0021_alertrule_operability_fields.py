"""Phase 2 operability fields:

- AlertRule.is_muted          — track alerts but suppress channel notifications
- AlertRule.renotify_interval_days — re-notify cadence for unresolved alerts
- AlertRule.last_fired_at     — when the engine last evaluated this rule
- AlertLog.last_notified_at   — when channels were last dispatched (drives re-notify)
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0020_phase1_alerting_rework'),
    ]

    operations = [
        migrations.AddField(
            model_name='alertrule',
            name='is_muted',
            field=models.BooleanField(
                default=False,
                help_text='Muted rules still track alerts in the Alert Center but send no channel notifications.',
            ),
        ),
        migrations.AddField(
            model_name='alertrule',
            name='renotify_interval_days',
            field=models.PositiveIntegerField(
                default=0,
                help_text='0 = notify once. N = re-send channel notifications every N days while an alert stays unresolved.',
            ),
        ),
        migrations.AddField(
            model_name='alertrule',
            name='last_fired_at',
            field=models.DateTimeField(
                null=True, blank=True, editable=False,
                help_text='When this rule was last evaluated by the engine.',
            ),
        ),
        migrations.AlterField(
            model_name='alertrule',
            name='is_active',
            field=models.BooleanField(
                default=True,
                help_text='Inactive rules are not evaluated at all.',
            ),
        ),
        migrations.AddField(
            model_name='alertlog',
            name='last_notified_at',
            field=models.DateTimeField(
                null=True, blank=True,
                help_text='When channel notifications were last dispatched for this alert (drives re-notify).',
            ),
        ),
    ]
