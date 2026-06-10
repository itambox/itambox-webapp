from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0023_remove_event_eventrule_webhookendpoint'),
        ('extras', '0013_exporttemplate_labeltemplate'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name='ExportTemplate'),
                migrations.DeleteModel(name='LabelTemplate'),
            ],
            database_operations=[],
        ),
    ]
