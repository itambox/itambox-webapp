from core.search import SearchIndex, register_search
from assets.models import AssetMaintenance


@register_search()
class AssetMaintenanceIndex(SearchIndex):
    model = AssetMaintenance
    fields = ('notes', 'supplier__name', 'asset__name')
    order_by = ('-start_date',)
