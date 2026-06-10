from django.db import migrations, models
import django.db.models.deletion


def set_exclude_convention_on_existing(apps, schema_editor):
    """Existing rows used the old month-diff logic (exclude_purchase_month).
    New rows default to include_purchase_month — leave them; only patch the old ones."""
    Depreciation = apps.get_model('assets', 'Depreciation')
    Depreciation.objects.filter(convention='include_purchase_month').update(
        convention='exclude_purchase_month'
    )


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0041_category_audit_interval_months'),
    ]

    operations = [
        # --- Depreciation policy fields ---
        migrations.AddField(
            model_name='depreciation',
            name='method',
            field=models.CharField(
                choices=[('straight_line', 'Straight-line'), ('none', 'None (no depreciation)')],
                default='straight_line',
                max_length=20,
                verbose_name='Method',
            ),
        ),
        migrations.AddField(
            model_name='depreciation',
            name='convention',
            field=models.CharField(
                choices=[
                    ('exclude_purchase_month', 'Exclude purchase month (month diff)'),
                    ('include_purchase_month', 'Include purchase month (pro rata temporis)'),
                ],
                default='include_purchase_month',
                max_length=30,
                verbose_name='Convention',
                help_text='Determines whether the acquisition month counts as a full depreciation month.',
            ),
        ),
        migrations.AddField(
            model_name='depreciation',
            name='immediate_expense_threshold',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Assets with purchase cost at or below this amount are fully expensed in the month of acquisition (e.g. 800 for German GWG).',
                max_digits=12,
                null=True,
                verbose_name='Immediate expense threshold (GWG)',
            ),
        ),
        migrations.AddField(
            model_name='depreciation',
            name='description',
            field=models.TextField(blank=True, verbose_name='Description'),
        ),
        # Data migration: existing rows → exclude_purchase_month (preserve prior behavior)
        migrations.RunPython(
            set_exclude_convention_on_existing,
            migrations.RunPython.noop,
        ),
        # --- Asset new fields ---
        migrations.AddField(
            model_name='asset',
            name='depreciation_override',
            field=models.ForeignKey(
                blank=True,
                help_text='Override depreciation policy — leave empty to use the tenant default or asset-type schedule.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='asset_overrides',
                to='assets.depreciation',
                verbose_name='Depreciation override',
            ),
        ),
        migrations.AddField(
            model_name='asset',
            name='in_service_date',
            field=models.DateField(
                blank=True,
                null=True,
                help_text='Depreciation starts here; falls back to purchase date.',
                verbose_name='In-service date',
            ),
        ),
        migrations.AddField(
            model_name='asset',
            name='disposed_at',
            field=models.DateTimeField(blank=True, editable=False, null=True, verbose_name='Disposed at'),
        ),
        migrations.AddField(
            model_name='asset',
            name='disposal_value',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                editable=False,
                max_digits=10,
                null=True,
                verbose_name='Sign-off value',
            ),
        ),
    ]
