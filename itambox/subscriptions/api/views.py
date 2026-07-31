from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import router, transaction
from django.db.models import Count, Q
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from itambox.api.openapi import MATURITY_EXTENSION
from itambox.api.permissions import StrictTenantPermission, TokenPermissions
from itambox.api.viewsets import ITAMBoxModelViewSet
from itambox.capabilities import STABLE
from subscriptions.filters import ProviderFilterSet, SubscriptionAssignmentFilterSet, SubscriptionFilterSet
from subscriptions.models import Provider, Subscription, SubscriptionAssignment, SubscriptionStatusChoices

from .serializers import ProviderSerializer, SubscriptionAssignmentSerializer, SubscriptionSerializer


class SubscriptionRenewSerializer(drf_serializers.Serializer):
    renewal_date = drf_serializers.DateField()
    renewal_cost = drf_serializers.DecimalField(max_digits=12, decimal_places=2, required=False)


class SubscriptionCancelSerializer(drf_serializers.Serializer):
    cancellation_date = drf_serializers.DateField(required=False)
    reason = drf_serializers.CharField(required=False, allow_blank=True, default="")


class SubscriptionStatusCompatibilitySerializer(drf_serializers.Serializer):
    status = drf_serializers.CharField()

    def validate_status(self, value):
        if value not in SubscriptionStatusChoices.values:
            raise drf_serializers.ValidationError("Unknown subscription status.")
        return value


class SubscriptionLifecyclePermissions(TokenPermissions):
    def get_required_permissions(self, method, model):
        if getattr(self, "_current_action", None) in {"suspend", "resume", "renew", "cancel"}:
            method = "PATCH"
        return super().get_required_permissions(method, model)

    def has_permission(self, request, view):
        self._current_action = getattr(view, "action", None)
        return super().has_permission(request, view)

    def has_object_permission(self, request, view, obj):
        self._current_action = getattr(view, "action", None)
        return super().has_object_permission(request, view, obj)


class ProviderViewSet(ITAMBoxModelViewSet):
    """API ViewSet for managing subscription Providers."""

    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = Provider.objects.prefetch_related("tags").annotate(
        subscription_count=Count("subscriptions", filter=Q(subscriptions__deleted_at__isnull=True))
    )
    serializer_class = ProviderSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ProviderFilterSet


class SubscriptionViewSet(ITAMBoxModelViewSet):
    """API ViewSet for managing recurring Subscriptions."""

    permission_classes = [SubscriptionLifecyclePermissions, StrictTenantPermission]
    queryset = Subscription.objects.select_related("provider").prefetch_related("tags").all()
    serializer_class = SubscriptionSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = SubscriptionFilterSet

    @extend_schema(
        request=SubscriptionStatusCompatibilitySerializer,
        responses=SubscriptionSerializer,
        extensions={MATURITY_EXTENSION: STABLE},
    )
    @action(detail=True, methods=["patch"], url_path="status")
    def update_status(self, request, pk=None):
        subscription = self.get_object_with_snapshot()
        self._validate_etag(request, subscription)
        serializer = SubscriptionStatusCompatibilitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic(using=router.db_for_write(Subscription)):
            locked = self.get_queryset().select_for_update().get(pk=subscription.pk)
            self._validate_etag(request, locked)
            if serializer.validated_data["status"] != locked.status:
                raise drf_serializers.ValidationError(
                    {"status": "Use the explicit suspend, resume, renew, or cancel action."}
                )

        response = Response(SubscriptionSerializer(locked, context={"request": request}).data)
        if etag := self._get_etag(locked):
            response["ETag"] = etag
        return response

    def _execute_lifecycle_action(self, request, method_name, *args, **kwargs):
        subscription = self.get_object_with_snapshot()
        self._validate_etag(request, subscription)

        with transaction.atomic(using=router.db_for_write(Subscription)):
            locked = self.get_queryset().select_for_update().get(pk=subscription.pk)
            self._validate_etag(request, locked)
            try:
                getattr(locked, method_name)(*args, **kwargs)
            except DjangoValidationError as exc:
                raise drf_serializers.ValidationError(exc.messages) from exc

        qs = self.get_queryset().get(pk=subscription.pk)
        response = Response(SubscriptionSerializer(qs, context={"request": request}).data)
        if etag := self._get_etag(qs):
            response["ETag"] = etag
        return response

    @extend_schema(request=None, responses=SubscriptionSerializer, extensions={MATURITY_EXTENSION: STABLE})
    @action(detail=True, methods=["post"])
    def suspend(self, request, pk=None):
        return self._execute_lifecycle_action(request, "suspend")

    @extend_schema(request=None, responses=SubscriptionSerializer, extensions={MATURITY_EXTENSION: STABLE})
    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None):
        return self._execute_lifecycle_action(request, "resume")

    @extend_schema(
        request=SubscriptionRenewSerializer,
        responses=SubscriptionSerializer,
        extensions={MATURITY_EXTENSION: STABLE},
    )
    @action(detail=True, methods=["post"])
    def renew(self, request, pk=None):
        serializer = SubscriptionRenewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._execute_lifecycle_action(
            request,
            "renew",
            serializer.validated_data["renewal_date"],
            cost=serializer.validated_data.get("renewal_cost"),
        )

    @extend_schema(
        request=SubscriptionCancelSerializer,
        responses=SubscriptionSerializer,
        extensions={MATURITY_EXTENSION: STABLE},
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        serializer = SubscriptionCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._execute_lifecycle_action(request, "cancel", **serializer.validated_data)


class SubscriptionAssignmentViewSet(ITAMBoxModelViewSet):
    """API ViewSet for managing Subscription assignments to assets, locations, or users."""

    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = (
        SubscriptionAssignment.objects.select_related("subscription__provider", "content_type")
        .prefetch_related("assigned_object")
        .all()
    )
    serializer_class = SubscriptionAssignmentSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = SubscriptionAssignmentFilterSet
