from django.db import migrations


POLICIES = [
    {
        'name': 'IT-Hardware 36 Monate (AfA)',
        'months': 36,
        'method': 'straight_line',
        'convention': 'include_purchase_month',
        'description': 'AfA-Tabelle 2021 — Computer, Notebooks, Tablets: 3 Jahre',
    },
    {
        'name': 'Server 60 Monate (AfA)',
        'months': 60,
        'method': 'straight_line',
        'convention': 'include_purchase_month',
        'description': 'AfA-Tabelle 2021 — Server / Workstations: 5 Jahre',
    },
    {
        'name': 'Sofortabschreibung GWG (≤ 800 €)',
        'months': 1,
        'method': 'straight_line',
        'convention': 'include_purchase_month',
        'immediate_expense_threshold': '800.00',
        'description': 'Geringwertige Wirtschaftsgüter §6 Abs. 2 EStG — Sofortabschreibung bis 800 €',
    },
]


def seed_policies(apps, schema_editor):
    Depreciation = apps.get_model('assets', 'Depreciation')
    for p in POLICIES:
        Depreciation.objects.get_or_create(
            name=p['name'],
            defaults={k: v for k, v in p.items() if k != 'name'},
        )


def rollback_policies(apps, schema_editor):
    Depreciation = apps.get_model('assets', 'Depreciation')
    Depreciation.objects.filter(name__in=[p['name'] for p in POLICIES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0042_depreciation_v2'),
    ]

    operations = [
        migrations.RunPython(seed_policies, rollback_policies),
    ]
