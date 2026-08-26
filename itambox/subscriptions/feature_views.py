"""Subscriptions-owned generic-presentation provider."""

from django_tables2 import RequestConfig

from itambox.registry import (
    DetailContextInput,
    ListContextInput,
    ListFilterInput,
    ListParamsInput,
    ListParamsResult,
)
from subscriptions.models import SubscriptionAssignment
from subscriptions.tables import SubscriptionAssignmentTable


class _SubscriptionsGenericPresentationProvider:
    def resolve_list_params(self, input: ListParamsInput) -> ListParamsResult:
        return ListParamsResult(params=input.params, state={})

    def filter_list_queryset(self, input: ListFilterInput):
        return input.queryset

    def build_list_context(self, input: ListContextInput) -> dict[str, object]:
        return {}

    def build_detail_context(self, input: DetailContextInput) -> dict[str, object]:
        assignments = SubscriptionAssignment.objects.filter(
            content_type=input.content_type,
            object_id=input.obj.pk,
        ).select_related("subscription", "subscription__provider", "assigned_by")
        table = SubscriptionAssignmentTable(assignments, request=input.request)
        table.exclude = ("content_type", "object_id", "assigned_object")
        RequestConfig(input.request, paginate=False).configure(table)
        return {
            "has_subscriptions": True,
            "subscribable_content_type_id": input.content_type.pk,
            "subscription_assignments_table": table,
            "subscription_assignments_count": assignments.count(),
        }


SUBSCRIPTIONS_GENERIC_PRESENTATION_PROVIDER = _SubscriptionsGenericPresentationProvider()
