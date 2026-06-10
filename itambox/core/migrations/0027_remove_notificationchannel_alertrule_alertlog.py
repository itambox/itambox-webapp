from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0026_remove_reporttemplate_scheduledreport_reportgenerationarchive'),
        ('extras', '0022_fix_scheduledreport_channels_ref'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name='AlertLog'),
                migrations.DeleteModel(name='AlertRule'),
                migrations.DeleteModel(name='NotificationChannel'),
            ],
            database_operations=[],
        ),
    ]
