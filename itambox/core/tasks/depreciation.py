import logging
from django.utils import timezone

from assets.models import Asset
from assets.depreciation import compute_book_value

logger = logging.getLogger(__name__)


def calculate_depreciation():
    """
    Nightly materialisation: write compute_book_value() into Asset.current_book_value.
    Only assets whose value actually changed are updated (the 2-dp quantise in
    compute_book_value prevents spurious nightly writes).
    """
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
