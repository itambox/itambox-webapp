from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0025_remove_journalentry_bookmark_attachments'),
        ('extras', '0017_reporttemplate_scheduledreport_reportgenerationarchive'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name='ReportGenerationArchive'),
                migrations.DeleteModel(name='ScheduledReport'),
                migrations.DeleteModel(name='ReportTemplate'),
            ],
            database_operations=[],
        ),
    ]
