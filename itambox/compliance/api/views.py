from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError
from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied

from assets.models import AssetMaintenance
from compliance.audit_services import audit_asset, close_audit_session
from compliance.filters import (
    AssetAuditFilterSet,
    AssetMaintenanceFilterSet,
    AuditSessionFilterSet,
    CustodyReceiptFilterSet,
)
from compliance.models import AssetAudit, AuditSession, CustodyReceipt, CustodyTemplate
from core.managers import get_current_tenant
from itambox.api.viewsets import ITAMBoxModelViewSet
from itambox.middleware import get_current_user

from .serializers import (
    AssetAuditSerializer,
    AssetMaintenanceSerializer,
    AuditSessionSerializer,
    CustodyReceiptSerializer,
    CustodyTemplateSerializer,
)


def _scope_by_asset_tenant(queryset):
    """Tenant-scope a queryset that derives its tenant through `asset.tenant`.

    These models (CustodyReceipt, AssetAudit, AssetMaintenance) have no direct
    `tenant` field, so StrictTenantPermission cannot enforce a boundary and the
    default manager is not tenant-scoped — without this filter the list/detail
    endpoints return every tenant's rows.

    When a tenant is active, filter to that tenant's assets.  When no tenant is
    active, mirror the behaviour of TenantScopingQuerySet.filter_by_tenant():
    superusers keep an unscoped view (useful for admin / test contexts); all
    other authenticated principals get an empty queryset (fail-closed).
    """
    tenant = get_current_tenant()
    if tenant is not None:
        return queryset.filter(asset__tenant=tenant)
    user = get_current_user()
    if user is not None and not getattr(user, "is_superuser", False):
        return queryset.none()
    return queryset


class CustodyTemplateViewSet(ITAMBoxModelViewSet):
    # CustodyTemplate has a direct (nullable) tenant FK + TenantScopingSoftDeleteManager
    # with allow_global_tenant, so BaseViewSet.get_queryset() auto-applies
    # filter_by_tenant() — returning the active tenant's own templates plus the
    # shared global (tenant=None) templates. No custom get_queryset needed.
    queryset = (
        CustodyTemplate.objects.select_related("tenant", "tenant_group", "category").prefetch_related("tags").all()
    )
    serializer_class = CustodyTemplateSerializer


class CustodyReceiptViewSet(ITAMBoxModelViewSet):
    queryset = CustodyReceipt.objects.select_related("asset", "holder").all()
    serializer_class = CustodyReceiptSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = CustodyReceiptFilterSet

    def get_queryset(self):
        return _scope_by_asset_tenant(super().get_queryset())


class AssetMaintenanceViewSet(ITAMBoxModelViewSet):
    queryset = AssetMaintenance.objects.select_related("asset").all()
    serializer_class = AssetMaintenanceSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetMaintenanceFilterSet

    def get_queryset(self):
        return _scope_by_asset_tenant(super().get_queryset())


class AuditSessionViewSet(ITAMBoxModelViewSet):
    queryset = AuditSession.objects.select_related("location", "created_by").all()
    serializer_class = AuditSessionSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AuditSessionFilterSet

    def perform_create(self, serializer):
        if serializer.validated_data.get("status") == "completed":
            raise ValidationError("Audit sessions must be closed through the close service.")
        return super().perform_create(serializer)

    def perform_update(self, serializer):
        requested_status = serializer.validated_data.get("status")
        current = serializer.instance
        if current.status == "completed" and requested_status not in (None, "completed"):
            raise ValidationError("Completed audit sessions cannot be reopened.")
        if requested_status != "completed":
            return super().perform_update(serializer)

        with transaction.atomic():
            locked = self.get_queryset().select_for_update().get(pk=current.pk)
            serializer.instance = locked
            serializer.validated_data.pop("status", None)
            instance = serializer.save()
            try:
                close_audit_session(instance, user=self.request.user, request=self.request)
            except DjangoPermissionDenied as exc:
                raise DRFPermissionDenied(str(exc)) from exc
        serializer.instance = instance


class AssetAuditViewSet(ITAMBoxModelViewSet):
    queryset = AssetAudit.objects.select_related("asset", "auditor", "location", "status", "session").all()
    serializer_class = AssetAuditSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetAuditFilterSet

    def get_queryset(self):
        return _scope_by_asset_tenant(super().get_queryset())

    def perform_create(self, serializer):
        values = serializer.validated_data
        try:
            audit = audit_asset(
                values["asset"],
                user=self.request.user,
                session=values.get("session"),
                location=values["location"],
                status=values["status"],
                notes=values.get("notes", ""),
                verification_method=values.get("verification_method", "manual"),
            )
        except DjangoPermissionDenied as exc:
            raise DRFPermissionDenied(str(exc)) from exc
        serializer.instance = audit

    def perform_update(self, serializer):
        immutable = {"session", "asset", "location", "status", "auditor", "timestamp", "verification_method"}
        changed = immutable.intersection(serializer.validated_data)
        if changed:
            raise ValidationError("Audit observation provenance cannot be changed after creation.")
        super().perform_update(serializer)
