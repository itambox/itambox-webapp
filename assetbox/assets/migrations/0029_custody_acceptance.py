import secrets
from django.db import migrations, models
import assets.models


def generate_tokens_for_existing(apps, schema_editor):
    CustodyReceipt = apps.get_model('assets', 'CustodyReceipt')
    for receipt in CustodyReceipt.objects.filter(token__isnull=True):
        receipt.token = secrets.token_urlsafe(48)
        receipt.save(update_fields=['token'])


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0028_add_requestable_field'),
    ]

    operations = [
        migrations.AddField(
            model_name='custodyreceipt',
            name='acceptance_method',
            field=models.CharField(default='link', max_length=50),
        ),
        migrations.AddField(
            model_name='custodyreceipt',
            name='acceptance_status',
            field=models.CharField(choices=[('pending', 'Pending'), ('accepted', 'Accepted'), ('declined', 'Declined')], default='pending', max_length=20),
        ),
        migrations.AddField(
            model_name='custodyreceipt',
            name='accepted',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='custodyreceipt',
            name='accepted_date',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='custodyreceipt',
            name='created_date',
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        migrations.AddField(
            model_name='custodyreceipt',
            name='signature_data',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='custodyreceipt',
            name='signature_hash',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name='custodyreceipt',
            name='token',
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
        migrations.RunPython(
            generate_tokens_for_existing,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='custodyreceipt',
            name='token',
            field=models.CharField(default=assets.models.generate_token, max_length=64, unique=True),
        ),
        migrations.AlterField(
            model_name='custodyreceipt',
            name='signature_canvas',
            field=models.TextField(blank=True, help_text='Base64 canvas stroke vector string representation', null=True),
        ),
        migrations.AlterField(
            model_name='custodyreceipt',
            name='verification_hash',
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
    ]
