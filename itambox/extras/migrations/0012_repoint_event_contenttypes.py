from django.db import migrations

MOVED_MODELS = ('event', 'eventrule', 'webhookendpoint')


def _rewrite_perm(perm):
    app_label, _, codename = perm.partition('.')
    if app_label == 'core' and codename.rsplit('_', 1)[-1] in MOVED_MODELS:
        return f'extras.{codename}'
    return perm


def forwards(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    ContentType.objects.filter(app_label='core', model__in=MOVED_MODELS).update(app_label='extras')

    TenantRole = apps.get_model('organization', 'TenantRole')
    for role in TenantRole.objects.all():
        perms = role.permissions or []
        rewritten = [_rewrite_perm(p) for p in perms]
        if rewritten != perms:
            role.permissions = rewritten
            role.save(update_fields=['permissions'])


def backwards(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    ContentType.objects.filter(app_label='extras', model__in=MOVED_MODELS).update(app_label='core')

    TenantRole = apps.get_model('organization', 'TenantRole')
    for role in TenantRole.objects.all():
        perms = role.permissions or []
        rewritten = [
            p.replace('extras.', 'core.', 1)
            if p.startswith('extras.') and p.rsplit('_', 1)[-1] in MOVED_MODELS else p
            for p in perms
        ]
        if rewritten != perms:
            role.permissions = rewritten
            role.save(update_fields=['permissions'])


class Migration(migrations.Migration):

    dependencies = [
        ('extras', '0011_event_eventrule_webhookendpoint'),
        ('core', '0023_remove_event_eventrule_webhookendpoint'),
        ('organization', '0013_assetholder_custom_field_data_and_more'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
