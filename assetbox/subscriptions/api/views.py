from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import serializers as drf_serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response

from core.api.permissions import TokenPermissions, StrictTenantPermission
from core.api.viewsets import AssetBoxModelViewSet
from subscriptions.filters import ProviderFilterSet, SubscriptionFilterSet, SubscriptionAssignmentFilterSet
from subscriptions.models import Provider, Subscription, SubscriptionAssignment
from .serializers import ProviderSerializer, SubscriptionSerializer, SubscriptionAssignmentSerializer


class SubscriptionStatusSerializer(drf_serializers.Serializer):
    """Serializer for updating only the status of a Subscription."""

    status = drf_serializers.ChoiceField(choices=Subscription._meta.get_field('status').choices)

    def update(self, instance, validated_data):
        instance.status = validated_data['status']
        instance.save(update_fields=['status'])
        return instance


class ProviderViewSet(AssetBoxModelViewSet):
    """API ViewSet for managing subscription Providers."""

    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = Provider.objects.prefetch_related('tags').all()
    serializer_class = ProviderSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ProviderFilterSet


class SubscriptionViewSet(AssetBoxModelViewSet):
    """API ViewSet for managing recurring Subscriptions."""

    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = Subscription.objects.select_related('provider').prefetch_related('tags').all()
    serializer_class = SubscriptionSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = SubscriptionFilterSet

    @action(detail=True, methods=['patch'], url_path='status', serializer_class=SubscriptionStatusSerializer)
    def update_status(self, request, pk=None):
        """Action for updating subscription status only."""
        subscription = self.get_object()
        serializer = SubscriptionStatusSerializer(subscription, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(SubscriptionSerializer(subscription, context={'request': request}).data)


class SubscriptionAssignmentViewSet(AssetBoxModelViewSet):
    """API ViewSet for managing Subscription assignments to assets, locations, or users."""

    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = SubscriptionAssignment.objects.select_related(
        'subscription__provider', 'content_type'
    ).prefetch_related('assigned_object').all()
    serializer_class = SubscriptionAssignmentSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = SubscriptionAssignmentFilterSet
