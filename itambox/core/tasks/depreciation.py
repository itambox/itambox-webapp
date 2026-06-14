import logging
from django.utils import timezone

from assets.models import Asset
from assets.depreciation import compute_book_value
from core.tasks.context import TaskContext

logger = logging.getLogger(__name__)


def calculate_depreciation():
    """
    Nightly materialisation: write compute_book_value() into Asset.current_book_value.
    Only assets whose value actually changed are updated (the 2-dp quantise in
    compute_book_value prevents spurious nightly writes).

    TaskContext is entered with no tenant_id so the query spans all tenants
    (Asset.objects falls through to the unscoped queryset when no tenant/user
    context is set — correct for a cross-tenant scheduled task). No user_id is
    provided because this is a system-level nightly job with no actor user.

    current_book_value is a derived/materialised field recomputed deterministically
    every run. It is intentionally NOT change-logged on a per-asset basis — doing
    so via .save() loops would flood the audit log with low-signal nightly noise.
    bulk_update() bypasses ChangeLoggingMixin, which is acceptable here.
    TaskContext still provides a synthetic _request_id so any incidental ORM
    operations inside this task do not silently drop their change-log entries.

    OPEN QUESTION: if a specific actor user should be attributed (e.g. a dedicated
    system/robot user), pass user_id=<that user's pk> to TaskContext below.
    Currently left as None (system-level attribution).
    """
    # Cross-tenant: no tenant_id. No actor user for this scheduled system task.
    with TaskContext(tenant_id=None, user_id=None):
        now = timezone.now()
        assets_to_update = []

        assets = Asset.objects.select_related(
            'asset_type__depreciation',
            'depreciation_override',
            'tenant__default_depreciation',
            'status',
        ).filter(purchase_cost__isnull=False)

        for asset in assets:
            new_value = compute_book_value(asset)
            if new_value is None:
                continue
            if asset.current_book_value != new_value:
                asset.current_book_value = new_value
                asset.depreciation_updated_at = now
                assets_to_update.append(asset)

        if assets_to_update:
            Asset.objects.bulk_update(
                assets_to_update,
                ['current_book_value', 'depreciation_updated_at'],
                batch_size=1000,
            )
        return len(assets_to_update)
