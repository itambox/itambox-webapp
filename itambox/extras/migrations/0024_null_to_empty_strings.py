from django.db import migrations, models


def normalize_nulls(apps, schema_editor):
    CustomField = apps.get_model('extras', 'CustomField')
    CustomField.objects.filter(choices__isnull=True).update(choices='')

    ReportTemplate = apps.get_model('extras', 'ReportTemplate')
    ReportTemplate.objects.filter(group_by_field__isnull=True).update(group_by_field='')

    ScheduledReport = apps.get_model('extras', 'ScheduledReport')
    ScheduledReport.objects.filter(cron_expression__isnull=True).update(cron_expression='')
    ScheduledReport.objects.filter(last_status__isnull=True).update(last_status='')

    ReportGenerationArchive = apps.get_model('extras', 'ReportGenerationArchive')
    ReportGenerationArchive.objects.filter(error_message__isnull=True).update(error_message='')


class Migration(migrations.Migration):

    dependencies = [
        ('extras', '0023_align_alerting_field_metadata'),
    ]

    operations = [
        migrations.RunPython(normalize_nulls, migrations.RunPython.noop),

        migrations.AlterField(
            model_name='customfield',
            name='choices',
            field=models.TextField(blank=True, help_text="New-line separated list of choices (only for 'select' type)"),
        ),
        migrations.AlterField(
            model_name='reporttemplate',
            name='group_by_field',
            field=models.CharField(blank=True, help_text='Optional column key to group grid records under (e.g. location, status).', max_length=100),
        ),
        migrations.AlterField(
            model_name='scheduledreport',
            name='cron_expression',
            field=models.CharField(blank=True, help_text="Custom Cron Expression (e.g. '0 8 * * 1-5')", max_length=100),
        ),
        migrations.AlterField(
            model_name='scheduledreport',
            name='last_status',
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AlterField(
            model_name='reportgenerationarchive',
            name='error_message',
            field=models.TextField(blank=True),
        ),
    ]
