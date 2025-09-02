from core.search import SearchIndex, register_search
from .models import Accessory, Consumable, Kit


@register_search()
class AccessoryIndex(SearchIndex):
    model = Accessory
    fields = ('name', 'part_number', 'notes', 'manufacturer__name')
    order_by = ('name',)


@register_search()
class ConsumableIndex(SearchIndex):
    model = Consumable
    fields = ('name', 'part_number', 'notes', 'manufacturer__name')
    order_by = ('name',)


@register_search()
class KitIndex(SearchIndex):
    model = Kit
    fields = ('name', 'description')
    order_by = ('name',)
