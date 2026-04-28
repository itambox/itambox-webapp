# itambox/assets/search.py
from core.search import SearchIndex, register_search
from .models import Asset, AssetRole, Manufacturer, Supplier, Category, AssetRequest

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
    fields = ('name', 'website', 'contact_name', 'address', 'notes',)
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