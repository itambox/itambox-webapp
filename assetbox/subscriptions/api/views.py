from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import serializers as drf_serializers

from core.api.viewsets import AssetBoxReadOnlyModelViewSet
from subscriptions.models import Provider, Subscription, SubscriptionAssignment
from .serializers import ProviderSerializer, SubscriptionSerializer, SubscriptionAssignmentSerializer


class SubscriptionStatusSerializer(drf_serializers.Serializer):
    status = drf_serializers.ChoiceField(choices=Subscription._meta.get_field('status').choices)

    def update(self, instance, validated_data):
        instance.status = validated_data['status']
        instance.save(update_fields=['status'])
        return instance


class ProviderViewSet(AssetBoxReadOnlyModelViewSet):
    queryset = Provider.objects.prefetch_related('tags').all()
    serializer_class = ProviderSerializer


class SubscriptionViewSet(AssetBoxReadOnlyModelViewSet):
    queryset = Subscription.objects.select_related('provider').prefetch_related('tags').all()
    serializer_class = SubscriptionSerializer

    @action(detail=True, methods=['patch'], url_path='status', serializer_class=SubscriptionStatusSerializer)
    def update_status(self, request, pk=None):
        subscription = self.get_object()
        serializer = SubscriptionStatusSerializer(subscription, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(SubscriptionSerializer(subscription).data)


class SubscriptionAssignmentViewSet(AssetBoxReadOnlyModelViewSet):
    queryset = SubscriptionAssignment.objects.select_related(
        'subscription__provider', 'content_type'
    ).prefetch_related('assigned_object').all()
    serializer_class = SubscriptionAssignmentSerializer
