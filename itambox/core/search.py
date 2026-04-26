# core/search.py
from collections import defaultdict
from django.db.models import Q

# Registry for search indexes
SEARCH_INDEXES = defaultdict(list)

def register_search():
    """
    Decorator to register a SearchIndex class.
    """
    def _wrapper(cls):
        model = cls.model
        # Ensure the model attribute exists before accessing it
        if not model:
             raise TypeError(f"SearchIndex subclass {cls.__name__} must define a 'model' attribute.")
        SEARCH_INDEXES[model].append(cls())
        return cls
    return _wrapper

class SearchIndex:
    # Subclasses must define the model
    model = None
    # Subclasses must define fields to search
    fields = ()
    # Optional: Define fields for ordering results
    order_by = ()

    def __init__(self):
        if not self.model or not self.fields:
            raise NotImplementedError(
                f"SearchIndex subclass {self.__class__.__name__} must define 'model' and 'fields'."
            )

    def search(self, term, queryset=None):
        """
        Search the index's model fields for the given term.
        Returns a filtered queryset.
        """
        if queryset is None:
            queryset = self.model.objects.all()

        query = Q()
        for field_name in self.fields:
            # Simple case: direct field lookup
            lookup = f'{field_name}__icontains'
            query |= Q(**{lookup: term})
            # Add more sophisticated lookup logic here if needed (e.g., exact matches, related fields)

        # Apply ordering if defined
        if self.order_by:
             return queryset.filter(query).order_by(*self.order_by)
        else:
             return queryset.filter(query)

    def get_results(self, term, queryset=None):
        """
        Wrapper around search to potentially handle different return formats later.
        For now, just calls search.
        """
        return self.search(term, queryset)
