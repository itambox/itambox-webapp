# Repoint django_content_type rows for the moved CustomField/CustomFieldset
# models from app_label 'assets' to 'extras'.
#
# Updating the rows IN PLACE keeps every GenericFK reference (ObjectChange,
# JournalEntry, Bookmark, attachments, alert logs) valid, and auth.Permission
# rows automatically resolve to the new 'extras.*' permission strings. The one
# thing that stores the app label as text is TenantRole.permissions (JSON), so
# those strings are rewritten here too.
#
# On fresh databases none of these rows exist yet (content types are created
# from the final state after migrate) and every step is a no-op.

from django.db import migrations

MOVED_MODELS = ('customfield', 'customfieldset')


def _rewrite_perm(perm):
    app_label, _, codename = perm.partition('.')
    if app_label == 'assets' and codename.rsplit('_', 1)[-1] in MOVED_MODELS:
        return f'extras.{codename}'
    return perm


def forwards(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    ContentType.objects.filter(app_label='assets', model__in=MOVED_MODELS).update(app_label='extras')

    TenantRole = apps.get_model('organization', 'TenantRole')
    for role in TenantRole.objects.all():
        perms = role.permissions or []
        rewritten = [_rewrite_perm(p) for p in perms]
        if rewritten != perms:
            role.permissions = rewritten
            role.save(update_fields=['permissions'])


def backwards(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    ContentType.objects.filter(app_label='extras', model__in=MOVED_MODELS).update(app_label='assets')

    TenantRole = apps.get_model('organization', 'TenantRole')
    for role in TenantRole.objects.all():
        perms = role.permissions or []
        rewritten = [
            p.replace('extras.', 'assets.', 1)
            if p.startswith('extras.') and p.rsplit('_', 1)[-1] in MOVED_MODELS else p
            for p in perms
        ]
        if rewritten != perms:
            role.permissions = rewritten
            role.save(update_fields=['permissions'])


class Migration(migrations.Migration):

    dependencies = [
        ('extras', '0008_customfield_customfieldset'),
        ('assets', '0033_remove_customfieldset_fields_and_more'),
        ('organization', '0012_alter_contactrole_name_alter_contactrole_slug_and_more'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
