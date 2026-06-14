# itambox/assets/search.py
from core.search import SearchIndex, register_search
from .models import Asset, AssetRole, Manufacturer, Supplier, Category, AssetRequest, AssetMaintenance

@register_search()
class AssetIndex(SearchIndex):
    model = Asset
    fields = (
        'name',
        'asset_tag',
        'serial_number',
        'notes',
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

@register_search()
class SupplierIndex(SearchIndex):
    model = Supplier
    fields = ('name', 'website', 'address', 'notes',)
    order_by = ('name',)

@register_search()
class CategoryIndex(SearchIndex):
    model = Category
    fields = ('name', 'description',)
    order_by = ('name',)

@register_search()
class AssetRequestIndex(SearchIndex):
    model = AssetRequest
    fields = ('notes', 'response_notes',)
    order_by = ('-request_date',)

@register_search()
class AssetMaintenanceIndex(SearchIndex):
    model = AssetMaintenance
    fields = ('notes', 'supplier__name', 'asset__name')
    order_by = ('-start_date',)