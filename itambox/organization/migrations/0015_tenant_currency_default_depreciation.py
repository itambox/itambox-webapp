from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0042_depreciation_v2'),
        ('organization', '0014_null_to_empty_strings'),
    ]

    operations = [
        migrations.AddField(
            model_name='tenant',
            name='currency',
            field=models.CharField(
                default='EUR',
                help_text='ISO 4217 currency code used for value display (display only, no conversion).',
                max_length=3,
                verbose_name='Display currency',
            ),
        ),
        migrations.AddField(
            model_name='tenant',
            name='default_depreciation',
            field=models.ForeignKey(
                blank=True,
                help_text='Fallback policy applied to all assets that have no type-level schedule and no per-asset override.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='tenants_defaulting',
                to='assets.depreciation',
                verbose_name='Default depreciation policy',
            ),
        ),
    ]
