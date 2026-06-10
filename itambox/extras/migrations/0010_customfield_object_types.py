# Generalize custom fields: a CustomField now declares the models it applies
# to via object_types (M2M to ContentType), replacing the asset-specific
# model_level boolean. Existing fields are migrated:
#   model_level=True  -> applies to AssetType (a hardware spec)
#   model_level=False -> applies to Asset (a per-device detail)

from django.db import migrations, models


def forwards(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    CustomField = apps.get_model('extras', 'CustomField')

    if not CustomField.objects.exists():
        return

    asset_ct, _ = ContentType.objects.get_or_create(app_label='assets', model='asset')
    assettype_ct, _ = ContentType.objects.get_or_create(app_label='assets', model='assettype')

    for cf in CustomField.objects.all():
        cf.object_types.add(assettype_ct if cf.model_level else asset_ct)


def backwards(apps, schema_editor):
    CustomField = apps.get_model('extras', 'CustomField')
    for cf in CustomField.objects.all():
        cf.model_level = cf.object_types.filter(app_label='assets', model='assettype').exists()
        cf.save(update_fields=['model_level'])


class Migration(migrations.Migration):

    dependencies = [
        ('extras', '0009_repoint_customfield_contenttype'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='customfield',
            name='object_types',
            field=models.ManyToManyField(
                blank=True,
                help_text='The model(s) this field applies to. A field applying to Asset Type '
                          'describes a hardware specification; one applying to Asset describes '
                          'a per-device detail.',
                related_name='custom_fields',
                to='contenttypes.contenttype',
                verbose_name='Object Types',
            ),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField(
            model_name='customfield',
            name='model_level',
        ),
    ]
