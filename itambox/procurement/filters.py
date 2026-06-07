import django_filters
from core.filters import BaseFilterSet
from .models import PurchaseOrder

class PurchaseOrderFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(
        method='search',
        label='Search',
    )
    
    class Meta:
        model = PurchaseOrder
        fields = ['status', 'supplier', 'destination_location']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(order_number__icontains=value)
