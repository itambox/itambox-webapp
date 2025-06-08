# core/search_backends.py
from django.db.models import F, Value
from django.contrib.contenttypes.models import ContentType

from .search import SEARCH_INDEXES

# Simple wrapper class to hold search results consistently
class SearchResult:
    def __init__(self, obj, object_type_id):
        self.object = obj
        self._object_type_id = object_type_id

    def __str__(self):
        return str(self.object)

    # Delegate attribute access to the underlying object if needed
    # def __getattr__(self, name):
    #     return getattr(self.object, name)

class DatabaseBackend:
    """
    A simple search backend that queries the database directly using
    registered SearchIndex classes.
    """
    def search(self, term, user, obj_type=None, queryset=None):
        """
        Search registered models for the given term.

        :param term: The search term (string).
        :param user: The user performing the search (used for permissions if implemented later).
        :param obj_type: Optional ContentType label (e.g., "assets.asset") to restrict search.
        :param queryset: An optional base queryset (rarely used for global search).
        :return: A list of SearchResult wrapper instances.
        """
        results = []
        models_to_search = list(SEARCH_INDEXES.keys())

        # Filter models if obj_type is specified
        if obj_type:
            try:
                app_label, model_name = obj_type.lower().split('.')
                ct = ContentType.objects.get(app_label=app_label, model=model_name)
                model_to_search = ct.model_class()
                if model_to_search in SEARCH_INDEXES:
                    models_to_search = [model_to_search]
                else:
                    # If the specified type isn't registered for search, return no results
                    return []
            except (ContentType.DoesNotExist, ValueError):
                # Invalid obj_type format or model not found
                return []

        # Iterate through registered models and their indexes
        for model in models_to_search:
            # A model might have multiple indexes (less common), iterate through them
            for index in SEARCH_INDEXES.get(model, []):
                # Use the index's get_results method
                model_queryset = index.get_results(term, queryset)

                # Get ContentType for this model
                ct = ContentType.objects.get_for_model(model)

                # Wrap results in SearchResult object
                for obj in model_queryset:
                    results.append(SearchResult(obj=obj, object_type_id=ct.pk))

        # Consider adding permission filtering here later based on the 'user' parameter
        # For now, return all results found.
        # Also, apply distinct() if duplicates across indexes/fields are possible,
        # though usually handled by the ORM.

        # TODO: Implement result scoring/ranking if needed.
        # TODO: Implement pagination (likely in the view layer).

        return results

# Instantiate the default backend
search_backend = DatabaseBackend()
