# core/search_backends.py
import logging
from django.db.models import F, Value, Q
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import FieldError, EmptyResultSet
from django.conf import settings

logger = logging.getLogger(__name__)
from itambox.utils import get_content_type_by_natural_key

from .search import SEARCH_INDEXES

MAX_SEARCH_RESULTS_PER_MODEL = getattr(settings, 'MAX_SEARCH_RESULTS_PER_MODEL', 1000)
MAX_SEARCH_COUNT_PER_MODEL = getattr(settings, 'MAX_SEARCH_COUNT_PER_MODEL', 10000)


class DatabaseBackend:
    """
    A simple search backend that queries the database directly using
    registered SearchIndex classes.
    """
    def search(self, query, user=None, obj_types=None, lookup='icontains'):
        results = {}
        if not query:
            return results

        target_models = SEARCH_INDEXES.keys()
        if obj_types:
            filtered_models = []
            for obj_type_key in obj_types:
                ct = get_content_type_by_natural_key(obj_type_key)
                if ct and ct.model_class() in target_models:
                    filtered_models.append(ct.model_class())
            if not filtered_models:
                return {}
            target_models = filtered_models

        for model in target_models:
            search_fields = set()
            index_classes = SEARCH_INDEXES.get(model, [])
            
            if not index_classes:
                continue
                
            for index_class in index_classes:
                fields_to_add = getattr(index_class, 'fields', []) 
                search_fields.update(fields_to_add)

            if not search_fields:
                continue

            q_objects = Q()
            empty_scope = False
            for field_name in search_fields:
                try:
                    dummy_q = Q(**{f'{field_name}__{lookup}': query})
                    # Force Django query compilation to catch FieldErrors (e.g. invalid lookup on ForeignKey)
                    str(model.objects.filter(dummy_q).query)
                    q_objects |= dummy_q
                except FieldError:
                    logger.warning("Lookup '%s' not valid for field '%s' on %s. Skipping.", lookup, field_name, model.__name__)
                    continue
                except EmptyResultSet:
                    # The model's tenant-scoping manager resolves to no rows for the
                    # current principal (e.g. an empty pk__in), so query compilation
                    # short-circuits to "always empty". No matches are possible for this
                    # model regardless of field — skip it rather than 500 the search.
                    empty_scope = True
                    break

            # No valid/searchable scope for this model (all fields invalid, or the scope
            # is empty). Skip it — filtering on an empty Q() would match every row.
            if empty_scope or not q_objects:
                continue

            queryset = model.objects.filter(q_objects)

            capped_queryset = queryset[:MAX_SEARCH_RESULTS_PER_MODEL]
            try:
                count = min(queryset[:MAX_SEARCH_COUNT_PER_MODEL].count(), MAX_SEARCH_COUNT_PER_MODEL)
            except EmptyResultSet:
                continue

            if count > 0:
                results[model] = {
                    'queryset': capped_queryset,
                    'count': count,
                    'verbose_name': model._meta.verbose_name,
                    'verbose_name_plural': model._meta.verbose_name_plural,
                }

        return results


search_backend = DatabaseBackend()
