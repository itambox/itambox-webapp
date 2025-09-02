from core.search import SearchIndex, register_search
from .models import License, LicenseSeatAssignment


@register_search()
class LicenseIndex(SearchIndex):
    model = License
    fields = ('name', 'notes', 'product_key', 'order_number')
    order_by = ('name',)


@register_search()
class LicenseSeatAssignmentIndex(SearchIndex):
    model = LicenseSeatAssignment
    fields = ('notes',)
