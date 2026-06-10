from django.db import migrations, models


def normalize_nulls(apps, schema_editor):
    CustodyReceipt = apps.get_model('compliance', 'CustodyReceipt')
    CustodyReceipt.objects.filter(eula_text__isnull=True).update(eula_text='')
    CustodyReceipt.objects.filter(disclaimer__isnull=True).update(disclaimer='')
    CustodyReceipt.objects.filter(qms_reference__isnull=True).update(qms_reference='')
    CustodyReceipt.objects.filter(signature_data__isnull=True).update(signature_data='')
    CustodyReceipt.objects.filter(signature_hash__isnull=True).update(signature_hash='')
    CustodyReceipt.objects.filter(signature_canvas__isnull=True).update(signature_canvas='')


class Migration(migrations.Migration):

    dependencies = [
        ('compliance', '0011_remove_assetmaintenance'),
    ]

    operations = [
        migrations.RunPython(normalize_nulls, migrations.RunPython.noop),

        migrations.AlterField(
            model_name='custodyreceipt',
            name='eula_text',
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name='custodyreceipt',
            name='disclaimer',
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name='custodyreceipt',
            name='qms_reference',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AlterField(
            model_name='custodyreceipt',
            name='signature_data',
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name='custodyreceipt',
            name='signature_hash',
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AlterField(
            model_name='custodyreceipt',
            name='signature_canvas',
            field=models.TextField(blank=True, help_text='Base64 canvas stroke vector string representation'),
        ),
    ]
