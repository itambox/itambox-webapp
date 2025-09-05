from django.db import migrations


def update_deployed_status_type(apps, schema_editor):
    StatusLabel = apps.get_model('assets', 'StatusLabel')

    updated = StatusLabel.objects.filter(slug='in-use').update(type='deployed')

    if not updated:
        StatusLabel.objects.get_or_create(
            slug='deployed',
            defaults={
                'name': 'Deployed',
                'type': 'deployed',
                'color': '007bff',
                'description': 'Assets that are currently checked out / assigned.',
            }
        )


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0037_remove_accessory_notification_category_and_more'),
    ]

    operations = [
        migrations.RunPython(update_deployed_status_type, migrations.RunPython.noop),
    ]
