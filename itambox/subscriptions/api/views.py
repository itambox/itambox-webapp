from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Count, Q
from django.db import router, transaction
from rest_framework import serializers as drf_serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response

from itambox.api.permissions import TokenPermissions, StrictTenantPermission
from itambox.api.viewsets import ITAMBoxModelViewSet
from subscriptions.filters import ProviderFilterSet, SubscriptionFilterSet, SubscriptionAssignmentFilterSet
from subscriptions.models import Provider, Subscription, SubscriptionAssignment
from .serializers import ProviderSerializer, SubscriptionSerializer, SubscriptionAssignmentSerializer


class SubscriptionStatusSerializer(drf_serializers.Serializer):
    """Serializer for updating only the status of a Subscription."""

    status = drf_serializers.ChoiceField(choices=Subscription._meta.get_field('status').choices)

    def update(self, instance, validated_data):
        instance.status = validated_data['status']
        # `updated_at` (auto_now) bumps with the row, so save the status alongside
        # it; otherwise the optimistic-concurrency ETag would not advance and the
        # change-log diff would be recorded but the token left stale.
        instance.save(update_fields=['status', 'updated_at'])
        return instance


class ProviderViewSet(ITAMBoxModelViewSet):
    """API ViewSet for managing subscription Providers."""

    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = Provider.objects.prefetch_related('tags').annotate(
        subscription_count=Count('subscriptions', filter=Q(subscriptions__deleted_at__isnull=True))
    )
    serializer_class = ProviderSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ProviderFilterSet


class SubscriptionViewSet(ITAMBoxModelViewSet):
    """API ViewSet for managing recurring Subscriptions."""

    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = Subscription.objects.select_related('provider').prefetch_related('tags').all()
    serializer_class = SubscriptionSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = SubscriptionFilterSet

    @action(detail=True, methods=['patch'], url_path='status', serializer_class=SubscriptionStatusSerializer)
    def update_status(self, request, pk=None):
        """Update only the status of a subscription.

        Routed through the same optimistic-concurrency machinery as the standard
        ``update()`` so two concurrent status writes get a 412 instead of silent
        last-writer-wins, and the snapshot keeps the change-log diff accurate.
        """
        # Snapshot + require/match the caller's If-Match against the current row.
        subscription = self.get_object_with_snapshot()
        self._validate_etag(request, subscription)

        serializer = SubscriptionStatusSerializer(subscription, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Re-validate the ETag against a row-locked re-read so a write that slips
        # in between the read above and the save loses the race with a 412.
        with transaction.atomic(using=router.db_for_write(Subscription)):
            locked = Subscription.objects.select_for_update().get(pk=subscription.pk)
            self._validate_etag(request, locked)
            serializer.save()

        qs = self.get_queryset().get(pk=subscription.pk)
        response = Response(SubscriptionSerializer(qs, context={'request': request}).data)
        if etag := self._get_etag(qs):
            response['ETag'] = etag
        return response


class SubscriptionAssignmentViewSet(ITAMBoxModelViewSet):
    """API ViewSet for managing Subscription assignments to assets, locations, or users."""

    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = SubscriptionAssignment.objects.select_related(
        'subscription__provider', 'content_type'
    ).prefetch_related('assigned_object').all()
    serializer_class = SubscriptionAssignmentSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = SubscriptionAssignmentFilterSet
