# itambox/extras/search.py
from core.search import SearchIndex, register_search
from .models import Tag

@register_search()
class TagIndex(SearchIndex):
    model = Tag
    fields = (
        'name', 'slug', 'description',
    )
    order_by = ('name',) 