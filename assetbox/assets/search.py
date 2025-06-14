# assetbox/assets/search.py
from core.search import SearchIndex, register_search
from .models import Asset, AssetRole, Manufacturer

@register_search()
class AssetIndex(SearchIndex):
    model = Asset
    fields = (
        'name',           # Search by name
        'asset_tag',      # Search by asset tag
        'serial_number',  # Search by serial number
        'notes',          # Search in notes (Corrected from 'comments')
        # Optional related fields (uncomment if needed and ensure models support __str__)
        # 'model__name',
        # 'assetrole__name',
        # 'manufacturer__name',
        # 'location__name',
        # 'location__site__name', # Search site name via location
        # 'tenant__name', # If directly assigned or via location/site
    )
    order_by = ('name',)

@register_search()
class AssetRoleIndex(SearchIndex):
    model = AssetRole
    fields = ('name', 'description',)
    order_by = ('name',)

@register_search()
class ManufacturerIndex(SearchIndex):
    model = Manufacturer
    fields = ('name', 'description',)
    order_by = ('name',) 