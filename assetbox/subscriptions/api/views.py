from rest_framework import viewsets

from subscriptions.models import Provider, Subscription, SubscriptionAssignment
from .serializers import ProviderSerializer, SubscriptionSerializer, SubscriptionAssignmentSerializer

class ProviderViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only API endpoint for Providers."""
    queryset = Provider.objects.prefetch_related('tags').all()
    serializer_class = ProviderSerializer

class SubscriptionViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only API endpoint for Subscriptions."""
    queryset = Subscription.objects.select_related('provider').prefetch_related('tags').all()
    serializer_class = SubscriptionSerializer

class SubscriptionAssignmentViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only API endpoint for Subscription Assignments."""
    queryset = SubscriptionAssignment.objects.select_related(
        'subscription__provider', 'content_type'
    ).prefetch_related('assigned_object').all()
    serializer_class = SubscriptionAssignmentSerializer
    # Note: Filtering/searching on GFK might require custom implementation 