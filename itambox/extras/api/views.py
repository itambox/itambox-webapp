from django_filters.rest_framework import DjangoFilterBackend
from itambox.api.viewsets import ITAMBoxModelViewSet
from extras.models import (
    Tag, Dashboard, CustomField, CustomFieldset,
    EventRule, WebhookEndpoint, NotificationChannel, AlertRule,
)
from extras.filters import (
    TagFilter, CustomFieldFilterSet, CustomFieldsetFilterSet,
    EventRuleFilterSet, WebhookEndpointFilterSet, NotificationChannelFilterSet,
    AlertRuleFilterSet,
)
from .serializers import (
    TagSerializer, DashboardSerializer, CustomFieldSerializer, CustomFieldsetSerializer,
    EventRuleSerializer, WebhookEndpointSerializer, NotificationChannelSerializer,
    AlertRuleSerializer,
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
    queryset = CustomFieldset.objects.prefetch_related('fields').all()
    serializer_class = CustomFieldsetSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = CustomFieldsetFilterSet


class DashboardViewSet(ITAMBoxModelViewSet):
    serializer_class = DashboardSerializer

    def get_queryset(self):
        return Dashboard.objects.select_related('user').filter(user=self.request.user)


class WebhookEndpointViewSet(ITAMBoxModelViewSet):
    queryset = WebhookEndpoint.objects.all()
    serializer_class = WebhookEndpointSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = WebhookEndpointFilterSet


class EventRuleViewSet(ITAMBoxModelViewSet):
    queryset = EventRule.objects.select_related('model', 'webhook').all()
    serializer_class = EventRuleSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = EventRuleFilterSet


class NotificationChannelViewSet(ITAMBoxModelViewSet):
    queryset = NotificationChannel.objects.all()
    serializer_class = NotificationChannelSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = NotificationChannelFilterSet


class AlertRuleViewSet(ITAMBoxModelViewSet):
    queryset = AlertRule.objects.prefetch_related('channels').all()
    serializer_class = AlertRuleSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AlertRuleFilterSet

