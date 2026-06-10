from django.db import migrations, models


def normalize_nulls(apps, schema_editor):
    AssetHolder = apps.get_model('organization', 'AssetHolder')
    AssetHolder.objects.filter(email__isnull=True).update(email='')


class Migration(migrations.Migration):

    dependencies = [
        ('organization', '0013_assetholder_custom_field_data_and_more'),
    ]

    operations = [
        migrations.RunPython(normalize_nulls, migrations.RunPython.noop),

        migrations.AlterField(
            model_name='assetholder',
            name='email',
            field=models.EmailField(blank=True, max_length=254),
        ),
    ]
