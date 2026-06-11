from django.db import migrations


def disable_script_rules(apps, schema_editor):
    EventRule = apps.get_model('extras', 'EventRule')
    qs = EventRule.objects.filter(action_type='script')
    for rule in qs:
        config = rule.action_config or {}
        config['_removed_note'] = 'Script action removed; rule disabled. Scripts may return as a plugin hook post-1.0.'
        rule.action_config = config
        rule.enabled = False
        rule.save(update_fields=['action_config', 'enabled'])


class Migration(migrations.Migration):

    dependencies = [
        ('extras', '0025_objectwatch'),
    ]

    operations = [
        migrations.RunPython(disable_script_rules, migrations.RunPython.noop),
    ]
