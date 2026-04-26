from django.db import migrations

def seed_status_labels(apps, schema_editor):
    StatusLabel = apps.get_model('assets', 'StatusLabel')
    defaults = [
        ('Available', 'available', 'deployable', '28a745'),
        ('In Use', 'in-use', 'deployed', '007bff'),
        ('Pending Repair', 'pending-repair', 'pending', 'ffc107'),
        ('Retired', 'retired', 'archived', 'dc3545'),
        ('In Transit', 'in-transit', 'pending', '6f42c1'),
        ('Decommissioned', 'decommissioned', 'undeployable', '6c757d'),
        ('Quarantined', 'quarantined', 'pending', 'fd7e14'),
    ]
    for name, slug, stype, color in defaults:
        StatusLabel.objects.get_or_create(
            slug=slug,
            defaults={'name': name, 'type': stype, 'color': color}
        )

def rollback_status_labels(apps, schema_editor):
    StatusLabel = apps.get_model('assets', 'StatusLabel')
    slugs = ['available', 'in-use', 'pending-repair', 'retired', 'in-transit', 'decommissioned', 'quarantined']
    StatusLabel.objects.filter(slug__in=slugs).delete()

class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0002_initial'),
    ]

    operations = [
        migrations.RunPython(seed_status_labels, rollback_status_labels),
    ]
