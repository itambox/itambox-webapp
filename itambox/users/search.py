from core.search import SearchIndex, register_search
from .models import Token


@register_search()
class TokenIndex(SearchIndex):
    model = Token
    fields = ('description',)
