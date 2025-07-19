from django.conf import settings
from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.utils.urls import remove_query_param, replace_query_param

from core.api.exceptions import QuerySetNotOrdered


class AssetBoxPagination(LimitOffsetPagination):
    start_query_param = 'start'

    def __init__(self):
        self.default_limit = getattr(settings, 'PAGINATE_COUNT', 50)
        self.start = None
        self._page_length = 0
        self._last_pk = None

    def paginate_queryset(self, queryset, request, view=None):
        if isinstance(queryset, QuerySet) and not queryset.ordered:
            queryset = queryset.order_by('pk')

        self.start = self.get_start(request)
        self.limit = self.get_limit(request)
        self.request = request

        if self.start is not None:
            if self.offset_query_param in request.query_params:
                raise ValidationError(
                    _("'{start_param}' and '{offset_param}' are mutually exclusive.").format(
                        start_param=self.start_query_param,
                        offset_param=self.offset_query_param,
                    )
                )

            self.count = None
            self.offset = 0

            queryset = queryset.filter(pk__gte=self.start).order_by('pk')
            results = list(queryset[:self.limit]) if self.limit is not None else list(queryset)

            self._page_length = len(results)
            if results:
                self._last_pk = results[-1].pk if hasattr(results[-1], 'pk') else results[-1]['pk']

            return results

        if isinstance(queryset, QuerySet):
            self.count = self.get_queryset_count(queryset)
        else:
            self.count = len(queryset)

        self.offset = self.get_offset(request)

        if self.limit is not None and self.count > self.limit and self.template is not None:
            self.display_page_controls = True

        if self.count == 0 or self.offset > self.count:
            return list()

        if self.limit is not None:
            return list(queryset[self.offset:self.offset + self.limit])
        return list(queryset[self.offset:])

    def get_start(self, request):
        try:
            value = int(request.query_params[self.start_query_param])
            if value < 0:
                raise ValidationError(
                    _("Invalid '{param}' parameter: must be a non-negative integer.").format(
                        param=self.start_query_param,
                    )
                )
            return value
        except KeyError:
            return None
        except (ValueError, TypeError):
            raise ValidationError(
                _("Invalid '{param}' parameter: must be a non-negative integer.").format(
                    param=self.start_query_param,
                )
            )

    def get_limit(self, request):
        max_limit = self.default_limit
        MAX_PAGE_SIZE = getattr(settings, 'MAX_PAGE_SIZE', None)

        if MAX_PAGE_SIZE:
            max_limit = min(max_limit, MAX_PAGE_SIZE)

        if self.limit_query_param:
            try:
                limit = int(request.query_params[self.limit_query_param])
                if limit < 0:
                    raise ValueError()

                if limit == 0:
                    return None

                if MAX_PAGE_SIZE:
                    max_limit = min(MAX_PAGE_SIZE, limit)
                else:
                    max_limit = limit
            except (KeyError, ValueError):
                pass

        return max_limit

    def get_queryset_count(self, queryset):
        return queryset.count()

    def get_next_link(self):
        if self.limit is None:
            return None

        if self.start is not None:
            if self._page_length < self.limit:
                return None
            url = self.request.build_absolute_uri()
            url = replace_query_param(url, self.start_query_param, self._last_pk + 1)
            url = replace_query_param(url, self.limit_query_param, self.limit)
            url = remove_query_param(url, self.offset_query_param)
            return url

        return super().get_next_link()

    def get_previous_link(self):
        if self.limit is None:
            return None

        if self.start is not None:
            return None

        return super().get_previous_link()

    def get_schema_operation_parameters(self, view):
        parameters = super().get_schema_operation_parameters(view)
        parameters.append({
            'name': self.start_query_param,
            'required': False,
            'in': 'query',
            'description': 'Cursor-based pagination: return results with pk >= start, ordered by pk.',
            'schema': {
                'type': 'integer',
            },
        })
        return parameters


class StripCountAnnotationsPaginator(AssetBoxPagination):
    def get_queryset_count(self, queryset):
        cloned_queryset = queryset.all()
        cloned_queryset.query.annotations.clear()
        return cloned_queryset.count()
