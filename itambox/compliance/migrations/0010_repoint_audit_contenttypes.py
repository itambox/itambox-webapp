# Repoint django_content_type rows for AuditSession + AssetAudit from
# app_label 'assets' to 'compliance'. Updating in place keeps every GenericFK
# reference (ObjectChange, JournalEntry, Bookmark, AlertLog) valid, and
# auth.Permission rows resolve to the new 'compliance.*' strings.
# TenantRole.permissions JSON entries are rewritten here too.
#
# On fresh databases these rows don't exist yet and every step is a no-op.

from django.db import migrations

MOVED_MODELS = ('auditsession', 'assetaudit')


def _rewrite_perm(perm):
    app_label, _, codename = perm.partition('.')
    if app_label == 'assets' and codename.rsplit('_', 1)[-1] in MOVED_MODELS:
        return f'compliance.{codename}'
    return perm


def forwards(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    ContentType.objects.filter(app_label='assets', model__in=MOVED_MODELS).update(app_label='compliance')

    TenantRole = apps.get_model('organization', 'TenantRole')
    for role in TenantRole.objects.all():
        perms = role.permissions or []
        rewritten = [_rewrite_perm(p) for p in perms]
        if rewritten != perms:
            role.permissions = rewritten
            role.save(update_fields=['permissions'])


def backwards(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    ContentType.objects.filter(app_label='compliance', model__in=MOVED_MODELS).update(app_label='assets')

    TenantRole = apps.get_model('organization', 'TenantRole')
    for role in TenantRole.objects.all():
        perms = role.permissions or []
        rewritten = [
            p.replace('compliance.', 'assets.', 1)
            if p.startswith('compliance.') and p.rsplit('_', 1)[-1] in MOVED_MODELS else p
            for p in perms
        ]
        if rewritten != perms:
            role.permissions = rewritten
            role.save(update_fields=['permissions'])


class Migration(migrations.Migration):

    dependencies = [
        ('compliance', '0009_auditsession_assetaudit'),
        ('assets', '0037_remove_auditsession_created_by_and_more'),
        ('organization', '0013_assetholder_custom_field_data_and_more'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
