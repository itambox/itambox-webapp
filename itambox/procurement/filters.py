import django_filters
from django.db import models
from django.utils.translation import gettext_lazy as _
from core.filters import BaseFilterSet
from .models import PurchaseOrder, Contract

class PurchaseOrderFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(
        method='search',
        label=_('Search'),
    )

    class Meta:
        model = PurchaseOrder
        fields = ['status', 'supplier', 'destination_location']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(order_number__icontains=value)


class ContractFilterSet(BaseFilterSet):
    q = django_filters.CharFilter(
        method='search',
        label=_('Search'),
    )

    class Meta:
        model = Contract
        fields = ['status', 'contract_type', 'supplier']

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            models.Q(name__icontains=value) | models.Q(contract_number__icontains=value)
        )
