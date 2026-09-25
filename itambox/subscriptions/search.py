from core.search import SearchIndex, register_search

from .models import Subscription


@register_search()
class SubscriptionIndex(SearchIndex):
    model = Subscription
    fields = ("name", "description", "notes", "contract_reference")
    category = "Subscriptions"
    search_fields = ("name", "description", "notes", "contract_reference", "supplier__name", "cost_center__name")
