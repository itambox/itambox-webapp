"""Backfill phase-4 ownership fields (ADR-0001).

Stock pools take their owner from ``location.tenant``. A pool at a
tenant-less location cannot be owned and aborts the migration — run
``manage.py integrity_report`` and fix those locations first (this app is
pre-release; there is no compatibility path for owner-less pools).

Assignment provenance is reconstructed best-effort: ``source_tenant`` from
the from-location's tenant (falling back to the catalogue item's tenant),
``target_tenant`` from the single assignment target. Rows whose source
cannot be derived (no from-location and a global item) stay NULL — exactly
the "ambiguous" class the phase-1 integrity report surfaces for operator
review. ``resource_grant`` stays NULL for all pre-existing rows: history is
never auto-legitimized.
"""
from django.db import migrations
from django.db.models import OuterRef, Subquery


STOCK_MODELS = ('ComponentStock', 'AccessoryStock', 'ConsumableStock')
ASSIGNMENT_SPECS = (
    ('ComponentAllocation', 'component'),
    ('AccessoryAssignment', 'accessory'),
    ('ConsumableAssignment', 'consumable'),
)


def _tenant_of(model, fk_field):
    """Subquery: the tenant id of the row referenced by ``fk_field``."""
    return Subquery(
        model.objects.filter(pk=OuterRef(f'{fk_field}_id')).values('tenant_id')[:1]
    )


def backfill(apps, schema_editor):
    Location = apps.get_model('organization', 'Location')
    AssetHolder = apps.get_model('organization', 'AssetHolder')
    Asset = apps.get_model('assets', 'Asset')

    for model_name in STOCK_MODELS:
        model = apps.get_model('inventory', model_name)
        orphaned = model.objects.filter(location__tenant__isnull=True).count()
        if orphaned:
            raise RuntimeError(
                f'{model_name}: {orphaned} pool(s) sit at locations without a '
                f'tenant — no owner can be derived. Run "manage.py '
                f'integrity_report", assign those locations to tenants, and '
                f'migrate again.'
            )
        model.objects.update(tenant_id=_tenant_of(Location, 'location'))

    for model_name, item_attr in ASSIGNMENT_SPECS:
        model = apps.get_model('inventory', model_name)
        item_model = apps.get_model(
            'inventory', model._meta.get_field(item_attr).related_model.__name__
        )
        model.objects.filter(from_location__isnull=False).update(
            source_tenant_id=_tenant_of(Location, 'from_location'),
        )
        model.objects.filter(from_location__isnull=True).update(
            source_tenant_id=_tenant_of(item_model, item_attr),
        )
        model.objects.filter(assigned_holder__isnull=False).update(
            target_tenant_id=_tenant_of(AssetHolder, 'assigned_holder'),
        )
        model.objects.filter(assigned_location__isnull=False).update(
            target_tenant_id=_tenant_of(Location, 'assigned_location'),
        )
        model.objects.filter(assigned_asset__isnull=False).update(
            target_tenant_id=_tenant_of(Asset, 'assigned_asset'),
        )


def unfill(apps, schema_editor):
    # Reversal is dropping the derived values; 0012's reverse then drops the
    # columns themselves.
    for model_name in STOCK_MODELS:
        apps.get_model('inventory', model_name).objects.update(tenant_id=None)
    for model_name, _item_attr in ASSIGNMENT_SPECS:
        apps.get_model('inventory', model_name).objects.update(
            source_tenant_id=None, target_tenant_id=None, resource_grant_id=None,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0012_accessoryassignment_resource_grant_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill, unfill),
    ]
