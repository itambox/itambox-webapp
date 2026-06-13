from core.search import SearchIndex, register_search
from .models import Provider, Subscription


@register_search()
class ProviderIndex(SearchIndex):
    model = Provider
    fields = ('name', 'account_id', 'admin_notes')
    category = 'Subscriptions'


@register_search()
class SubscriptionIndex(SearchIndex):
    model = Subscription
    fields = ('name', 'description', 'notes', 'contract_reference', 'cost_center')
    category = 'Subscriptions'
    search_fields = ('name', 'description', 'notes', 'contract_reference', 'provider__name')
