from django.db import migrations, models


def normalize_nulls(apps, schema_editor):
    Manufacturer = apps.get_model('assets', 'Manufacturer')
    Manufacturer.objects.filter(description__isnull=True).update(description='')

    Asset = apps.get_model('assets', 'Asset')
    Asset.objects.filter(serial_number__isnull=True).update(serial_number='')
    Asset.objects.filter(notes__isnull=True).update(notes='')

    Supplier = apps.get_model('assets', 'Supplier')
    Supplier.objects.filter(website__isnull=True).update(website='')
    Supplier.objects.filter(contact_email__isnull=True).update(contact_email='')
    Supplier.objects.filter(contact_phone__isnull=True).update(contact_phone='')
    Supplier.objects.filter(address__isnull=True).update(address='')
    Supplier.objects.filter(contact_name__isnull=True).update(contact_name='')
    Supplier.objects.filter(notes__isnull=True).update(notes='')

    Category = apps.get_model('assets', 'Category')
    Category.objects.filter(color__isnull=True).update(color='')
    Category.objects.filter(description__isnull=True).update(description='')

    AssetRequest = apps.get_model('assets', 'AssetRequest')
    AssetRequest.objects.filter(notes__isnull=True).update(notes='')
    AssetRequest.objects.filter(response_notes__isnull=True).update(response_notes='')


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0039_repoint_assetmaintenance_contenttype'),
    ]

    operations = [
        migrations.RunPython(normalize_nulls, migrations.RunPython.noop),

        migrations.AlterField(
            model_name='manufacturer',
            name='description',
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name='asset',
            name='serial_number',
            field=models.CharField(blank=True, db_index=True, max_length=100),
        ),
        migrations.AlterField(
            model_name='asset',
            name='notes',
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name='supplier',
            name='website',
            field=models.URLField(blank=True, max_length=500),
        ),
        migrations.AlterField(
            model_name='supplier',
            name='contact_email',
            field=models.EmailField(blank=True, max_length=255),
        ),
        migrations.AlterField(
            model_name='supplier',
            name='contact_phone',
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AlterField(
            model_name='supplier',
            name='address',
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name='supplier',
            name='contact_name',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AlterField(
            model_name='supplier',
            name='notes',
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name='category',
            name='color',
            field=models.CharField(blank=True, help_text='RGB color in hexadecimal (e.g. 00ff00)', max_length=6),
        ),
        migrations.AlterField(
            model_name='category',
            name='description',
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name='assetrequest',
            name='notes',
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name='assetrequest',
            name='response_notes',
            field=models.TextField(blank=True),
        ),
    ]
