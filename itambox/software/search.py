from core.search import SearchIndex, register_search
from .models import Software


@register_search()
class SoftwareIndex(SearchIndex):
    model = Software
    fields = ('name', 'description', 'version', 'manufacturer__name')
    order_by = ('name',)
