from django_filters.rest_framework import DjangoFilterBackend

from extras.filters import (
    AlertRuleFilterSet,
    CustomFieldFilterSet,
    CustomFieldsetFilterSet,
    EventRuleFilterSet,
    JournalEntryFilterSet,
    NotificationChannelFilterSet,
    TagFilter,
    WebhookEndpointFilterSet,
)
from extras.models import (
    AlertRule,
    CustomField,
    CustomFieldset,
    Dashboard,
    EventRule,
    JournalEntry,
    NotificationChannel,
    Tag,
    WebhookEndpoint,
)
from itambox.api.viewsets import ITAMBoxModelViewSet

from .serializers import (
    AlertRuleSerializer,
    CustomFieldSerializer,
    CustomFieldsetSerializer,
    DashboardSerializer,
    EventRuleSerializer,
    JournalEntrySerializer,
    NotificationChannelSerializer,
    TagSerializer,
    WebhookEndpointSerializer,
)


class TagViewSet(ITAMBoxModelViewSet):
    queryset = Tag.objects.all()
    serializer_class = TagSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = TagFilter


class CustomFieldViewSet(ITAMBoxModelViewSet):
    queryset = CustomField.objects.all()
    serializer_class = CustomFieldSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = CustomFieldFilterSet


class CustomFieldsetViewSet(ITAMBoxModelViewSet):
    queryset = CustomFieldset.objects.prefetch_related("fields").all()
    serializer_class = CustomFieldsetSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = CustomFieldsetFilterSet


class DashboardViewSet(ITAMBoxModelViewSet):
    serializer_class = DashboardSerializer

    def get_queryset(self):
        return Dashboard.objects.select_related("user").filter(user=self.request.user)


class WebhookEndpointViewSet(ITAMBoxModelViewSet):
    queryset = WebhookEndpoint.objects.all()
    serializer_class = WebhookEndpointSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = WebhookEndpointFilterSet


class EventRuleViewSet(ITAMBoxModelViewSet):
    queryset = EventRule.objects.select_related("model", "webhook").all()
    serializer_class = EventRuleSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = EventRuleFilterSet


class NotificationChannelViewSet(ITAMBoxModelViewSet):
    queryset = NotificationChannel.objects.all()
    serializer_class = NotificationChannelSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = NotificationChannelFilterSet


class AlertRuleViewSet(ITAMBoxModelViewSet):
    queryset = AlertRule.objects.prefetch_related("channels").all()
    serializer_class = AlertRuleSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AlertRuleFilterSet


class JournalEntryViewSet(ITAMBoxModelViewSet):
    queryset = JournalEntry.objects.select_related("model", "user", "tenant").all()
    serializer_class = JournalEntrySerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = JournalEntryFilterSet
