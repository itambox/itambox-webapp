import logging
import datetime
from decimal import Decimal
from django.utils import timezone

from assets.models import Asset

logger = logging.getLogger(__name__)

def calculate_depreciation():
    """
    Cron job to calculate straight-line depreciation and materialize it 
    into the current_book_value field on all applicable assets.
    """
    today = datetime.date.today()
    now = timezone.now()
    
    # Only process assets with a purchase cost, purchase date, and an asset type with depreciation
    assets_to_update = []
    
    assets = Asset.objects.select_related('asset_type__depreciation').filter(
        purchase_cost__isnull=False,
        purchase_date__isnull=False,
        asset_type__depreciation__isnull=False
    )
    
    for asset in assets:
        deprec = asset.asset_type.depreciation
        if deprec.months <= 0:
            new_value = asset.purchase_cost
        else:
            months_held = (today.year - asset.purchase_date.year) * 12 + today.month - asset.purchase_date.month
            
            if months_held <= 0:
                new_value = asset.purchase_cost
            else:
                salvage = asset.salvage_value or Decimal('0.00')
                if months_held >= deprec.months:
                    new_value = salvage
                else:
                    depreciable_base = asset.purchase_cost - salvage
                    monthly_depreciation = depreciable_base / Decimal(str(deprec.months))
                    current = asset.purchase_cost - (monthly_depreciation * Decimal(str(months_held)))
                    new_value = max(current, salvage)
        
        # Round to 2 decimal places
        new_value = new_value.quantize(Decimal('0.01'))
        
        if asset.current_book_value != new_value:
            asset.current_book_value = new_value
            asset.depreciation_updated_at = now
            assets_to_update.append(asset)
            
    if assets_to_update:
        # bulk update in chunks to avoid blowing up memory/query size
        Asset.objects.bulk_update(
            assets_to_update, 
            ['current_book_value', 'depreciation_updated_at'], 
            batch_size=1000
        )
    return len(assets_to_update)
