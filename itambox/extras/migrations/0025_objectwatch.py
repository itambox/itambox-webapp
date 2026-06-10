"""
Add ObjectWatch model and seed it from existing Bookmark rows.

Existing bookmarks were created by users who received change notifications via
_notify_bookmark_subscribers. To preserve that behaviour after the split, every
existing Bookmark row is copied into an ObjectWatch row (the Bookmark row is kept
too). After migration users have both a star and a bell on items they had starred;
they can unwatch individually without losing their bookmark.
"""
import core.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def copy_bookmarks_to_watches(apps, schema_editor):
    Bookmark = apps.get_model('extras', 'Bookmark')
    ObjectWatch = apps.get_model('extras', 'ObjectWatch')
    db_alias = schema_editor.connection.alias

    watches = []
    for b in Bookmark.objects.using(db_alias).all():
        watches.append(ObjectWatch(
            user_id=b.user_id,
            model_id=b.model_id,
            object_id=b.object_id,
        ))

    # ignore_conflicts so a re-run of data migration is safe
    ObjectWatch.objects.using(db_alias).bulk_create(watches, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('extras', '0024_null_to_empty_strings'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ObjectWatch',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('object_id', models.PositiveBigIntegerField()),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('model', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='contenttypes.contenttype')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='watches', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Object Watch',
                'verbose_name_plural': 'Object Watches',
                'ordering': ['-created'],
                'indexes': [models.Index(fields=['user', 'model', 'object_id'], name='extras_watch_user_id_idx')],
            },
            bases=(core.models.ChangeLoggingMixin, models.Model),
        ),
        migrations.AddConstraint(
            model_name='objectwatch',
            constraint=models.UniqueConstraint(
                fields=('user', 'model', 'object_id'),
                name='extras_objectwatch_unique_user_model_object',
            ),
        ),
        migrations.RunPython(copy_bookmarks_to_watches, migrations.RunPython.noop),
    ]
