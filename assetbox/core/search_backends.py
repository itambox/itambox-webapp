# core/search_backends.py
from django.db.models import F, Value, Q
from django.contrib.contenttypes.models import ContentType
from .utils import get_content_type_by_natural_key # Assuming this exists

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
    def search(self, query, user=None, obj_types=None, lookup='icontains'):
        """
        Search registered models for the given query.
        
        Args:
            query (str): The search term.
            user: The current user (optional, for permissions).
            obj_types (list): Optional list of natural keys of object types (e.g., ["assets.asset", "organization.site"]).
            lookup (str): The Django ORM lookup type (e.g., 'icontains', 'iexact').
        """
        results = {}
        if not query:
            return results

        # Determine target models
        target_models = SEARCH_INDEXES.keys()
        if obj_types:
            filtered_models = []
            for obj_type_key in obj_types:
                ct = get_content_type_by_natural_key(obj_type_key)
                if ct and ct.model_class() in target_models:
                    filtered_models.append(ct.model_class())
            if not filtered_models:
                return {} # No valid types selected
            target_models = filtered_models

        for model in target_models:
            search_fields = set() # Use a set to collect unique fields
            index_classes = SEARCH_INDEXES.get(model, []) # Get the list of index classes
            
            if not index_classes:
                continue
                
            # Collect fields from all index classes for this model
            for index_class in index_classes:
                # Assuming index classes have a 'fields' attribute or similar
                # If the structure is different (e.g., a method get_search_fields()), adjust this line
                fields_to_add = getattr(index_class, 'fields', []) 
                search_fields.update(fields_to_add)

            if not search_fields:
                continue

            # Construct Q object using the specified lookup and collected fields
            q_objects = Q()
            for field_name in search_fields:
                # Add check for valid lookup type for the field if necessary
                try:
                    q_objects |= Q(**{f'{field_name}__{lookup}': query})
                except FieldError:
                    # Handle cases where lookup isn't valid for a field type (e.g., regex on non-char)
                    print(f"[Search] Warning: Lookup '{lookup}' not valid for field '{field_name}' on {model.__name__}. Skipping.")
                    continue 

            # Apply query
            queryset = model.objects.filter(q_objects)
            count = queryset.count()

            if count > 0:
                # TODO: Consider permissions filtering here if needed
                # queryset = queryset.restrict(user, 'view') # If using a permissions manager
                results[model] = {
                    'queryset': queryset,
                    'count': count,
                    'verbose_name': model._meta.verbose_name,
                    'verbose_name_plural': model._meta.verbose_name_plural,
                    # Add list URL if available
                    # 'list_url': reverse(f"{model._meta.app_label}:{model._meta.model_name}_list"),
                }
                
        return results

# Instantiate the default backend
search_backend = DatabaseBackend()
