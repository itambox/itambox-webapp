import core.models
import core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('core', '0024_remove_exporttemplate_labeltemplate'),
        ('extras', '0014_repoint_exporttemplate_contenttypes'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='JournalEntry',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('object_id', models.PositiveBigIntegerField(db_index=True)),
                        ('created', models.DateTimeField(auto_now_add=True, db_index=True)),
                        ('comment', models.TextField()),
                        ('model', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='journal_entries', to='contenttypes.contenttype')),
                        ('user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='journal_entries', to=settings.AUTH_USER_MODEL)),
                    ],
                    options={
                        'verbose_name': 'Journal Entry',
                        'verbose_name_plural': 'Journal Entries',
                        'ordering': ['-created'],
                        'indexes': [models.Index(fields=['model', 'object_id'], name='core_journa_model_i_3f2f97_idx')],
                    },
                    bases=(core.models.ChangeLoggingMixin, models.Model),
                ),
                migrations.CreateModel(
                    name='Bookmark',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('object_id', models.PositiveBigIntegerField()),
                        ('created', models.DateTimeField(auto_now_add=True)),
                        ('model', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='contenttypes.contenttype')),
                        ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='bookmarks', to=settings.AUTH_USER_MODEL)),
                    ],
                    options={
                        'verbose_name': 'Bookmark',
                        'verbose_name_plural': 'Bookmarks',
                        'ordering': ['-created'],
                        'indexes': [models.Index(fields=['user', 'model', 'object_id'], name='core_bookma_user_id_69a2d6_idx')],
                    },
                    bases=(core.models.ChangeLoggingMixin, models.Model),
                ),
                migrations.AddConstraint(
                    model_name='bookmark',
                    constraint=models.UniqueConstraint(fields=('user', 'model', 'object_id'), name='core_bookmark_unique_user_model_object'),
                ),
                migrations.CreateModel(
                    name='ImageAttachment',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('object_id', models.PositiveBigIntegerField(db_index=True)),
                        ('image', models.ImageField(upload_to='attachments/images/', validators=[core.validators.validate_image_attachment])),
                        ('name', models.CharField(blank=True, max_length=255)),
                        ('created', models.DateTimeField(auto_now_add=True)),
                        ('model', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='image_attachments', to='contenttypes.contenttype')),
                    ],
                    options={
                        'verbose_name': 'Image Attachment',
                        'verbose_name_plural': 'Image Attachments',
                        'ordering': ['-created'],
                        'indexes': [models.Index(fields=['model', 'object_id'], name='core_imagea_model_i_684849_idx')],
                    },
                    bases=(core.models.ChangeLoggingMixin, models.Model),
                ),
                migrations.CreateModel(
                    name='FileAttachment',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('object_id', models.PositiveBigIntegerField(db_index=True)),
                        ('file', models.FileField(upload_to='attachments/files/', validators=[core.validators.validate_file_attachment])),
                        ('name', models.CharField(blank=True, max_length=255)),
                        ('mime_type', models.CharField(blank=True, max_length=100)),
                        ('created', models.DateTimeField(auto_now_add=True)),
                        ('model', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='file_attachments', to='contenttypes.contenttype')),
                    ],
                    options={
                        'verbose_name': 'File Attachment',
                        'verbose_name_plural': 'File Attachments',
                        'ordering': ['-created'],
                        'indexes': [models.Index(fields=['model', 'object_id'], name='core_fileat_model_i_c8edb4_idx')],
                    },
                    bases=(core.models.ChangeLoggingMixin, models.Model),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    'ALTER TABLE core_journalentry RENAME TO extras_journalentry',
                    reverse_sql='ALTER TABLE extras_journalentry RENAME TO core_journalentry',
                ),
                migrations.RunSQL(
                    'ALTER TABLE core_bookmark RENAME TO extras_bookmark',
                    reverse_sql='ALTER TABLE extras_bookmark RENAME TO core_bookmark',
                ),
                migrations.RunSQL(
                    'ALTER TABLE core_imageattachment RENAME TO extras_imageattachment',
                    reverse_sql='ALTER TABLE extras_imageattachment RENAME TO core_imageattachment',
                ),
                migrations.RunSQL(
                    'ALTER TABLE core_fileattachment RENAME TO extras_fileattachment',
                    reverse_sql='ALTER TABLE extras_fileattachment RENAME TO core_fileattachment',
                ),
            ],
        ),
    ]
