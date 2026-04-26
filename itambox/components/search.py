from core.search import SearchIndex, register_search
from .models import Component


@register_search()
class ComponentIndex(SearchIndex):
    model = Component
    fields = ('name', 'part_number', 'description', 'manufacturer__name')
    order_by = ('name',)
