import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0024_remove_exporttemplate_labeltemplate'),
        ('extras', '0015_journalentry_bookmark_attachments'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                # Must repoint the FK before deleting FileAttachment from core state
                migrations.AlterField(
                    model_name='reportgenerationarchive',
                    name='file',
                    field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='report_archives', to='extras.fileattachment'),
                ),
                migrations.DeleteModel(name='JournalEntry'),
                migrations.DeleteModel(name='Bookmark'),
                migrations.DeleteModel(name='ImageAttachment'),
                migrations.DeleteModel(name='FileAttachment'),
            ],
            database_operations=[],
        ),
    ]
