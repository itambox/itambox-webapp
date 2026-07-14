# Repoint django_content_type rows for InstalledSoftware from app_label
# 'assets' to 'software'. Updating in place keeps every GenericFK reference
# (ObjectChange, JournalEntry, Bookmark, attachments, AlertLog) valid, and
# auth.Permission rows resolve to the new 'software.*' permission strings.
# TenantRole.permissions JSON entries are rewritten here too.
#
# On fresh databases these rows don't exist yet and every step is a no-op.

from django.db import migrations

MOVED_MODELS = ('installedsoftware',)


def _rewrite_perm(perm):
    app_label, _, codename = perm.partition('.')
    if app_label == 'assets' and codename.rsplit('_', 1)[-1] in MOVED_MODELS:
        return f'software.{codename}'
    return perm


def forwards(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    ContentType.objects.filter(app_label='assets', model__in=MOVED_MODELS).update(app_label='software')

    TenantRole = apps.get_model('organization', 'TenantRole')
    for role in TenantRole.objects.all():
        perms = role.permissions or []
        rewritten = [_rewrite_perm(p) for p in perms]
        if rewritten != perms:
            role.permissions = rewritten
            role.save(update_fields=['permissions'])


def backwards(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    ContentType.objects.filter(app_label='software', model__in=MOVED_MODELS).update(app_label='assets')

    TenantRole = apps.get_model('organization', 'TenantRole')
    for role in TenantRole.objects.all():
        perms = role.permissions or []
        rewritten = [
            p.replace('software.', 'assets.', 1)
            if p.startswith('software.') and p.rsplit('_', 1)[-1] in MOVED_MODELS else p
            for p in perms
        ]
        if rewritten != perms:
            role.permissions = rewritten
            role.save(update_fields=['permissions'])


class Migration(migrations.Migration):

    # This data migration reads organization.TenantRole from historical
    # state; pin it BEFORE the migration that drops the model, so new
    # migrations elsewhere can never re-shuffle the plan past the drop.
    run_before = [
        ('organization', '0027_drop_legacy_role_models'),
    ]

    dependencies = [
        ('software', '0007_installedsoftware'),
        ('assets', '0036_remove_installedsoftware'),
        ('organization', '0012_alter_contactrole_name_alter_contactrole_slug_and_more'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
