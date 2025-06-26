from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import serializers as drf_serializers

from subscriptions.models import Provider, Subscription, SubscriptionAssignment
from .serializers import ProviderSerializer, SubscriptionSerializer, SubscriptionAssignmentSerializer


class SubscriptionStatusSerializer(drf_serializers.Serializer):
    """Slim serializer for PATCHing only the status field."""
    status = drf_serializers.ChoiceField(choices=Subscription._meta.get_field('status').choices)

    def update(self, instance, validated_data):
        instance.status = validated_data['status']
        instance.save(update_fields=['status'])
        return instance


class ProviderViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only API endpoint for Providers."""
    queryset = Provider.objects.prefetch_related('tags').all()
    serializer_class = ProviderSerializer


class SubscriptionViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only API endpoint for Subscriptions with a PATCH status action."""
    queryset = Subscription.objects.select_related('provider').prefetch_related('tags').all()
    serializer_class = SubscriptionSerializer

    @action(detail=True, methods=['patch'], url_path='status', serializer_class=SubscriptionStatusSerializer)
    def update_status(self, request, pk=None):
        """PATCH /api/subscriptions/subscriptions/{pk}/status/ — update only the status field."""
        subscription = self.get_object()
        serializer = SubscriptionStatusSerializer(subscription, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        # Return full subscription data after update
        return Response(SubscriptionSerializer(subscription).data)


class SubscriptionAssignmentViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only API endpoint for Subscription Assignments."""
    queryset = SubscriptionAssignment.objects.select_related(
        'subscription__provider', 'content_type'
    ).prefetch_related('assigned_object').all()
    serializer_class = SubscriptionAssignmentSerializer 