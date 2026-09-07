from django.db import models, transaction
from django.db.models import Prefetch
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from assets.filters import (
    AssetFilterSet,
    AssetRequestFilterSet,
    AssetReservationFilterSet,
    AssetRoleFilterSet,
    AssetTagSequenceFilterSet,
    AssetTypeFilterSet,
    CategoryFilterSet,
    DepreciationFilterSet,
    ManufacturerFilterSet,
    StatusLabelFilterSet,
    SupplierFilterSet,
    WarrantyFilterSet,
)
from assets.models import (
    Asset,
    AssetAssignment,
    AssetDisposal,
    AssetRequest,
    AssetReservation,
    AssetRole,
    AssetTagSequence,
    AssetType,
    AssetTypeFieldset,
    Category,
    Depreciation,
    Manufacturer,
    StatusLabel,
    Supplier,
    Warranty,
)
from assets.services import checkin_asset, checkout_asset
from assets.services.specifications._command_support import issue, lock_relevant_libraries, resource_revision_for_owner
from assets.services.specifications.commands import (
    apply_category_defaults,
    cleanup_asset_history,
    cleanup_asset_type_history,
    preview_asset_history_cleanup,
    preview_asset_type_history_cleanup,
    set_asset_type_composition,
    set_category_defaults,
)
from assets.services.specifications.contracts import (
    CommandRejectedDTO,
    DefinitionRevision,
    OwnerRefDTO,
    ResourceRevision,
)
from assets.services.specifications.locking import catalogue_transaction_lock
from assets.specification_adapters import actor_context_for_user
from itambox.api.permissions import StrictTenantPermission, TokenPermissions
from itambox.api.viewsets import ITAMBoxModelViewSet

from .serializers import (
    AssetAssignmentSerializer,
    AssetCheckInAPISerializer,
    AssetCheckOutAPISerializer,
    AssetDisposalSerializer,
    AssetRequestSerializer,
    AssetReservationSerializer,
    AssetRoleSerializer,
    AssetSerializer,
    AssetTagSequenceSerializer,
    AssetTypeSerializer,
    CategorySerializer,
    DepreciationSerializer,
    ManufacturerSerializer,
    StatusLabelSerializer,
    SupplierSerializer,
    WarrantySerializer,
)
from .specification_api import (
    ApplyCategoryDefaultsInputSerializer,
    CategoryDefaultFieldsetsInputSerializer,
    CompositionInputSerializer,
    HistoryCleanupInputSerializer,
    HistoryCleanupWriteInputSerializer,
    SpecificationCommandAPIException,
    asset_history_authorization_for_user,
    category_default_payload,
    command_result_response,
    composition_preview_payload,
    create_missing_precondition_paths,
    definition_for_owner,
    error_response,
    etag_for_owner,
    etag_for_revision,
    explicit_fieldset_selection,
    history_keys,
    if_match_revision,
    missing_precondition_response,
    patch_from_validated,
    preview_result_response,
)


class SpecificationContractMixin:
    """Use the canonical resource digest for specification-aware objects."""

    @staticmethod
    def _owner_ref(instance):
        if isinstance(instance, AssetType):
            return OwnerRefDTO("asset_type", instance.pk)
        if isinstance(instance, Asset):
            return OwnerRefDTO("asset", instance.pk)
        if isinstance(instance, Category):
            return OwnerRefDTO("category", instance.pk)
        return None

    @staticmethod
    def _get_etag(instance):
        return etag_for_owner(instance)

    def _raise_etag_issue(self, instance, code, path):
        owner = self._owner_ref(instance)
        raise SpecificationCommandAPIException(
            CommandRejectedDTO(
                outcome="rejected",
                safe_owner=owner,
                issues=(issue(code, path=path),),
            )
        )

    def _validate_etag(self, request, instance):
        provided = if_match_revision(request)
        if provided is None:
            self._raise_etag_issue(instance, "MISSING_PRECONDITION", ("If-Match",))
        current = str(resource_revision_for_owner(instance))
        if provided != current:
            self._raise_etag_issue(instance, "STALE_RESOURCE", ("If-Match",))


class SpecificationActionPermissions(TokenPermissions):
    """Map command preview/cleanup POSTs to the change capability."""

    CHANGE_ACTIONS = {
        "composition_preview",
        "composition",
        "apply_category_defaults",
        "apply_category_defaults_preview",
        "specification_history_cleanup_preview",
        "specification_history_cleanup",
        "default_fieldsets",
    }
    _current_action = None

    def get_required_permissions(self, method, model):
        if method != "GET" and self._current_action in self.CHANGE_ACTIONS:
            method = "PATCH"
        return super().get_required_permissions(method, model)

    def has_permission(self, request, view):
        self._current_action = getattr(view, "action", None)
        return super().has_permission(request, view)

    def has_object_permission(self, request, view, obj):
        self._current_action = getattr(view, "action", None)
        return super().has_object_permission(request, view, obj)


class AssetStateActionPermissions(TokenPermissions):
    """checkout/checkin mutate asset state, so they require change_<model> rather than the
    POST-default add_<model> (TokenPermissions maps POST->add, PATCH->change). All the base
    tenant-resolution logic is reused; only the action->perm mapping is adjusted."""

    STATE_CHANGE_ACTIONS = {
        "checkout",
        "checkin",
        "specification_history_cleanup_preview",
        "specification_history_cleanup",
    }
    _current_action = None

    def get_required_permissions(self, method, model):
        if self._current_action in self.STATE_CHANGE_ACTIONS:
            method = "PATCH"
        return super().get_required_permissions(method, model)

    def has_permission(self, request, view):
        self._current_action = getattr(view, "action", None)
        return super().has_permission(request, view)

    def has_object_permission(self, request, view, obj):
        self._current_action = getattr(view, "action", None)
        return super().has_object_permission(request, view, obj)


class SpecificationCommandUpdateMixin(SpecificationContractMixin):
    # Preserve the legacy ETag lock without taking it before command locks.

    def update(self, request, *args, **kwargs):
        data = request.data
        if isinstance(data, dict):
            needs_definition = "specification_patch" in data
            if "asset_type_id" in data and not needs_definition:
                submitted_type_id = data.get("asset_type_id")
                if isinstance(submitted_type_id, bool):
                    submitted_type_id = None
                elif isinstance(submitted_type_id, str) and submitted_type_id.isdecimal():
                    submitted_type_id = int(submitted_type_id)
                if isinstance(submitted_type_id, int) and submitted_type_id > 0:
                    current = self.get_object()
                    needs_definition = submitted_type_id != current.asset_type_id
            if needs_definition and not data.get("expected_definition_revision"):
                return missing_precondition_response(("expected_definition_revision",))
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        data = serializer.validated_data
        type_changed = (
            isinstance(serializer.instance, Asset)
            and "asset_type" in data
            and data["asset_type"].pk != serializer.instance.asset_type_id
        )
        if "specification_patch" not in data and not type_changed:
            return super().perform_update(serializer)
        # The generic view locks the owner before invoking serializer.save().
        # Supported REST mutations use the value-command shared catalogue lock.
        # A concurrent owner Type switch changes its ETag; the generic view
        # rechecks that required precondition under its owner lock before save.
        # Native-only requests retain the unchanged generic persistence path.
        with transaction.atomic(), catalogue_transaction_lock(exclusive=False):
            self._lock_command_libraries(serializer)
            return super().perform_update(serializer)

    @staticmethod
    def _lock_command_libraries(serializer):
        owner = serializer.instance
        data = serializer.validated_data
        if isinstance(owner, Asset):
            current_type_id = Asset._base_manager.filter(pk=owner.pk).values_list("asset_type_id", flat=True).first()
            target_type_id = getattr(data.get("asset_type"), "pk", None)
            type_ids = tuple(sorted({value for value in (current_type_id, target_type_id) if value is not None}))
            lock_relevant_libraries(type_ids, "asset")
        else:
            lock_relevant_libraries((owner.pk,), "asset_type")


class AssetViewSet(SpecificationCommandUpdateMixin, ITAMBoxModelViewSet):
    permission_classes = [AssetStateActionPermissions, StrictTenantPermission]
    queryset = Asset.objects.select_related("asset_role", "asset_type__manufacturer", "location").prefetch_related(
        Prefetch("assignments", queryset=AssetAssignment.objects.filter(is_active=True), to_attr="_active_assignments"),
        Prefetch(
            "asset_type__fieldset_memberships",
            queryset=AssetTypeFieldset.objects.select_related("fieldset").prefetch_related(
                "fieldset__field_memberships__custom_field__object_types",
                "fieldset__field_memberships__custom_field__choice_set__choices",
            ),
        ),
    )
    serializer_class = AssetSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetFilterSet

    @extend_schema(
        request=AssetCheckOutAPISerializer,
        responses={
            200: {"type": "object", "properties": {"status": {"type": "string"}, "message": {"type": "string"}}}
        },
    )
    @action(detail=True, methods=["post"], serializer_class=AssetCheckOutAPISerializer)
    def checkout(self, request, pk=None):
        """
        API Action to check out an asset.
        """
        asset = self.get_object()
        serializer = self.get_serializer(data=request.data, context={"asset": asset})
        serializer.is_valid(raise_exception=True)

        target = checkout_asset(
            asset=asset,
            user=request.user,
            holder=serializer.validated_data.get("holder"),
            location=serializer.validated_data.get("location"),
            asset_target=serializer.validated_data.get("asset_target"),
            expected_checkin=serializer.validated_data.get("expected_checkin"),
            notes=serializer.validated_data.get("notes", ""),
            status=serializer.validated_data.get("status_id"),
        )

        return Response({"status": "success", "message": f"Asset checked out to {target}."}, status=status.HTTP_200_OK)

    @extend_schema(
        request=AssetCheckInAPISerializer,
        responses={
            200: {"type": "object", "properties": {"status": {"type": "string"}, "message": {"type": "string"}}}
        },
    )
    @action(detail=True, methods=["post"], serializer_class=AssetCheckInAPISerializer)
    def checkin(self, request, pk=None):
        """
        API Action to check in an asset.
        """
        asset = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        checkin_asset(
            asset=asset,
            user=request.user,
            notes=serializer.validated_data.get("notes", ""),
            status=serializer.validated_data.get("status_id"),
            location=serializer.validated_data.get("location_id"),
            checkin_date=serializer.validated_data.get("checkin_date"),
        )
        return Response(
            {"status": "success", "message": f"Asset {asset.asset_tag} checked in successfully."},
            status=status.HTTP_200_OK,
        )

    @action(
        detail=True,
        methods=["post"],
        url_path="specification-history/cleanup-preview",
        serializer_class=HistoryCleanupInputSerializer,
    )
    def specification_history_cleanup_preview(self, request, pk=None):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        resource_revision = if_match_revision(request)
        expected_definition_revision = serializer.validated_data.get("expected_definition_revision")
        if resource_revision is None:
            return missing_precondition_response(("If-Match",))
        if not expected_definition_revision:
            return missing_precondition_response(("expected_definition_revision",))
        asset = self.get_object()
        authorization = asset_history_authorization_for_user(user=request.user, tenant_id=asset.tenant_id)
        if authorization is None:
            return error_response((issue("OBJECT_UNAVAILABLE", message_key="specifications.object_unavailable"),))
        try:
            result = preview_asset_history_cleanup(
                authorization=authorization,
                asset_id=asset.pk,
                keys=history_keys(serializer.validated_data["keys"]),
                expected_resource_revision=ResourceRevision(resource_revision),
                expected_definition_revision=DefinitionRevision(expected_definition_revision),
            )
        except (TypeError, ValueError):
            return error_response(
                (issue("DUPLICATE_FIELD", path=("keys",), message_key="specifications.duplicate_field"),)
            )
        return preview_result_response(result)

    @action(
        detail=True,
        methods=["post"],
        url_path="specification-history/cleanup",
        serializer_class=HistoryCleanupWriteInputSerializer,
    )
    def specification_history_cleanup(self, request, pk=None):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        resource_revision = if_match_revision(request)
        required = ("preview_token", "expected_definition_revision")
        missing = [name for name in required if not serializer.validated_data.get(name)]
        if resource_revision is None:
            missing.insert(0, "If-Match")
        if missing:
            return missing_precondition_response(*((name,) for name in missing))
        asset = self.get_object()
        authorization = asset_history_authorization_for_user(user=request.user, tenant_id=asset.tenant_id)
        if authorization is None:
            return error_response((issue("OBJECT_UNAVAILABLE", message_key="specifications.object_unavailable"),))
        try:
            result = cleanup_asset_history(
                authorization=authorization,
                asset_id=asset.pk,
                keys=history_keys(serializer.validated_data["keys"]),
                preview_token=serializer.validated_data["preview_token"],
                expected_resource_revision=ResourceRevision(resource_revision),
                expected_definition_revision=DefinitionRevision(
                    serializer.validated_data["expected_definition_revision"]
                ),
            )
        except (TypeError, ValueError):
            return error_response(
                (issue("DUPLICATE_FIELD", path=("keys",), message_key="specifications.duplicate_field"),)
            )
        response = command_result_response(result)
        if hasattr(result, "resource_revision"):
            response["ETag"] = etag_for_revision(result.resource_revision)
        return response


class AssetRoleViewSet(ITAMBoxModelViewSet):
    queryset = AssetRole.objects.annotate(asset_count=models.Count("assets"))
    serializer_class = AssetRoleSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetRoleFilterSet


class ManufacturerViewSet(ITAMBoxModelViewSet):
    queryset = Manufacturer.objects.annotate(asset_count=models.Count("asset_types__assets"))
    serializer_class = ManufacturerSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = ManufacturerFilterSet


class AssetTypeViewSet(SpecificationCommandUpdateMixin, ITAMBoxModelViewSet):
    permission_classes = [SpecificationActionPermissions]
    queryset = AssetType.objects.select_related("manufacturer").prefetch_related(
        "tags",
        Prefetch(
            "fieldset_memberships",
            queryset=AssetTypeFieldset.objects.select_related("fieldset").prefetch_related(
                "fieldset__field_memberships__custom_field__object_types",
                "fieldset__field_memberships__custom_field__choice_set__choices",
            ),
        ),
    )
    serializer_class = AssetTypeSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetTypeFilterSet

    def create(self, request, *args, **kwargs):
        paths = create_missing_precondition_paths(request.data)
        if paths:
            return missing_precondition_response(*paths)
        return super().create(request, *args, **kwargs)

    @action(detail=True, methods=["get"], url_path="specification-definition")
    def specification_definition(self, request, pk=None):
        target = request.query_params.get("target")
        if target not in {"asset_type", "asset"}:
            return error_response(
                (
                    issue(
                        "INVALID_TYPE",
                        path=("target",),
                        message_key="specifications.invalid_type",
                    ),
                )
            )
        owner = self.get_object()
        try:
            return Response(definition_for_owner(owner, target), status=status.HTTP_200_OK)
        except (TypeError, ValueError):
            return error_response(
                (
                    issue(
                        "UNSUPPORTED_STRUCTURE",
                        path=("target",),
                        message_key="specifications.unsupported_structure",
                    ),
                )
            )

    @action(
        detail=True,
        methods=["post"],
        url_path="composition-preview",
        serializer_class=CompositionInputSerializer,
    )
    def composition_preview(self, request, pk=None):
        owner = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            selection = explicit_fieldset_selection(serializer.validated_data["fieldsets"])
            patch = patch_from_validated(serializer.validated_data.get("specification_patch"))
            return Response(
                composition_preview_payload(owner, selection.identities, patch),
                status=status.HTTP_200_OK,
            )
        except (TypeError, ValueError):
            return error_response(
                (
                    issue(
                        "REFERENCE_CONFLICT",
                        path=("fieldsets",),
                        message_key="specifications.reference_conflict",
                    ),
                )
            )

    @action(
        detail=True,
        methods=["put"],
        url_path="composition",
        serializer_class=CompositionInputSerializer,
    )
    def composition(self, request, pk=None):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        resource_revision = if_match_revision(request)
        expected_definition_revision = serializer.validated_data.get("expected_definition_revision")
        if resource_revision is None:
            return missing_precondition_response(("If-Match",))
        if not expected_definition_revision:
            return missing_precondition_response(("expected_definition_revision",))
        owner = self.get_object()
        try:
            result = set_asset_type_composition(
                actor=actor_context_for_user(request.user),
                asset_type_id=owner.pk,
                fieldsets=explicit_fieldset_selection(serializer.validated_data["fieldsets"]),
                expected_resource_revision=ResourceRevision(resource_revision),
                expected_definition_revision=DefinitionRevision(expected_definition_revision),
                patch=patch_from_validated(serializer.validated_data.get("specification_patch")),
            )
        except (TypeError, ValueError):
            return error_response(
                (
                    issue(
                        "INVALID_TYPE",
                        path=("fieldsets",),
                        message_key="specifications.invalid_type",
                    ),
                )
            )
        response = command_result_response(result)
        if hasattr(result, "resource_revision"):
            response["ETag"] = etag_for_revision(result.resource_revision)
        return response

    @action(
        detail=True,
        methods=["post"],
        url_path="apply-category-defaults",
        serializer_class=ApplyCategoryDefaultsInputSerializer,
    )
    def apply_category_defaults(self, request, pk=None):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        resource_revision = if_match_revision(request)
        required = (
            "preview_token",
            "expected_definition_revision",
            "expected_category_default_snapshot_revision",
        )
        missing = [name for name in required if not serializer.validated_data.get(name)]
        if resource_revision is None:
            missing.insert(0, "If-Match")
        if missing:
            return missing_precondition_response(*((name,) for name in missing))
        owner = self.get_object()
        result = apply_category_defaults(
            actor=actor_context_for_user(request.user),
            asset_type_id=owner.pk,
            preview_token=serializer.validated_data["preview_token"],
            expected_resource_revision=ResourceRevision(resource_revision),
            expected_definition_revision=DefinitionRevision(serializer.validated_data["expected_definition_revision"]),
            expected_category_default_snapshot_revision=serializer.validated_data[
                "expected_category_default_snapshot_revision"
            ],
            patch=patch_from_validated(serializer.validated_data.get("specification_patch")),
        )
        response = command_result_response(result)
        if hasattr(result, "resource_revision"):
            response["ETag"] = etag_for_revision(result.resource_revision)
        return response

    @action(
        detail=True,
        methods=["post"],
        url_path="specification-history/cleanup-preview",
        serializer_class=HistoryCleanupInputSerializer,
    )
    def specification_history_cleanup_preview(self, request, pk=None):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        resource_revision = if_match_revision(request)
        expected_definition_revision = serializer.validated_data.get("expected_definition_revision")
        if resource_revision is None:
            return missing_precondition_response(("If-Match",))
        if not expected_definition_revision:
            return missing_precondition_response(("expected_definition_revision",))
        owner = self.get_object()
        try:
            result = preview_asset_type_history_cleanup(
                actor=actor_context_for_user(request.user),
                asset_type_id=owner.pk,
                keys=history_keys(serializer.validated_data["keys"]),
                expected_resource_revision=ResourceRevision(resource_revision),
                expected_definition_revision=DefinitionRevision(expected_definition_revision),
            )
        except (TypeError, ValueError):
            return error_response(
                (issue("DUPLICATE_FIELD", path=("keys",), message_key="specifications.duplicate_field"),)
            )
        return preview_result_response(result)

    @action(
        detail=True,
        methods=["post"],
        url_path="specification-history/cleanup",
        serializer_class=HistoryCleanupWriteInputSerializer,
    )
    def specification_history_cleanup(self, request, pk=None):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        resource_revision = if_match_revision(request)
        required = ("preview_token", "expected_definition_revision")
        missing = [name for name in required if not serializer.validated_data.get(name)]
        if resource_revision is None:
            missing.insert(0, "If-Match")
        if missing:
            return missing_precondition_response(*((name,) for name in missing))
        owner = self.get_object()
        try:
            result = cleanup_asset_type_history(
                actor=actor_context_for_user(request.user),
                asset_type_id=owner.pk,
                keys=history_keys(serializer.validated_data["keys"]),
                preview_token=serializer.validated_data["preview_token"],
                expected_resource_revision=ResourceRevision(resource_revision),
                expected_definition_revision=DefinitionRevision(
                    serializer.validated_data["expected_definition_revision"]
                ),
            )
        except (TypeError, ValueError):
            return error_response(
                (issue("DUPLICATE_FIELD", path=("keys",), message_key="specifications.duplicate_field"),)
            )
        response = command_result_response(result)
        if hasattr(result, "resource_revision"):
            response["ETag"] = etag_for_revision(result.resource_revision)
        return response


class StatusLabelViewSet(ITAMBoxModelViewSet):
    queryset = StatusLabel.objects.prefetch_related("tags").all()
    serializer_class = StatusLabelSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = StatusLabelFilterSet


class DepreciationViewSet(ITAMBoxModelViewSet):
    queryset = Depreciation.objects.all()
    serializer_class = DepreciationSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = DepreciationFilterSet


class SupplierViewSet(ITAMBoxModelViewSet):
    queryset = Supplier.objects.prefetch_related("tags").all()
    serializer_class = SupplierSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = SupplierFilterSet


class CategoryViewSet(SpecificationContractMixin, ITAMBoxModelViewSet):
    permission_classes = [SpecificationActionPermissions]
    queryset = Category.objects.prefetch_related("tags").all()
    serializer_class = CategorySerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = CategoryFilterSet

    @action(
        detail=True,
        methods=["get", "put"],
        url_path="default-fieldsets",
        serializer_class=CategoryDefaultFieldsetsInputSerializer,
    )
    def default_fieldsets(self, request, pk=None):
        category = self.get_object()
        if request.method == "GET":
            response = Response(category_default_payload(category), status=status.HTTP_200_OK)
            response["ETag"] = etag_for_owner(category)
            return response

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        resource_revision = if_match_revision(request)
        if resource_revision is None:
            return missing_precondition_response(("If-Match",))
        try:
            result = set_category_defaults(
                actor=actor_context_for_user(request.user),
                category_id=category.pk,
                expected_resource_revision=ResourceRevision(resource_revision),
                fieldsets=explicit_fieldset_selection(serializer.validated_data["fieldsets"]),
            )
        except (TypeError, ValueError):
            return error_response(
                (issue("REFERENCE_CONFLICT", path=("fieldsets",), message_key="specifications.reference_conflict"),)
            )
        response = command_result_response(result)
        if hasattr(result, "resource_revision"):
            response["ETag"] = etag_for_revision(result.resource_revision)
        return response


class AssetRequestViewSet(ITAMBoxModelViewSet):
    queryset = (
        AssetRequest.objects.select_related("requester", "asset", "asset_type__manufacturer", "responded_by")
        .prefetch_related("tags")
        .all()
    )
    serializer_class = AssetRequestSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetRequestFilterSet


class AssetTagSequenceViewSet(ITAMBoxModelViewSet):
    queryset = AssetTagSequence.objects.all()
    serializer_class = AssetTagSequenceSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetTagSequenceFilterSet


class AssetAssignmentViewSet(ITAMBoxModelViewSet):
    queryset = AssetAssignment.objects.select_related(
        "asset", "checked_out_by", "checked_in_by", "assigned_user", "assigned_location", "assigned_asset"
    ).prefetch_related("tags")
    serializer_class = AssetAssignmentSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ["asset_id", "is_active", "checked_out_by_id", "assigned_user_id"]


class AssetDisposalViewSet(ITAMBoxModelViewSet):
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = AssetDisposal.objects.select_related(
        "asset",
        "asset__asset_type__manufacturer",
        "asset__tenant",
    )
    serializer_class = AssetDisposalSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ["asset_id", "disposal_method", "data_sanitization_method", "weee_compliant"]


class WarrantyViewSet(ITAMBoxModelViewSet):
    # Warranty has no direct `tenant` field — it derives tenant through
    # `asset.tenant` (tenant_lookup='asset__tenant'). StrictTenantPermission
    # therefore cannot enforce an object-level boundary on its own; the scope is
    # applied by BaseViewSet.get_queryset re-running the manager's
    # filter_by_tenant(), which honours tenant_lookup. Mirrors AssetDisposalViewSet.
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = Warranty.objects.select_related(
        "asset",
        "asset__asset_type__manufacturer",
        "asset__tenant",
    )
    serializer_class = WarrantySerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = WarrantyFilterSet


class AssetReservationViewSet(ITAMBoxModelViewSet):
    # Like Warranty, AssetReservation derives tenant through `asset.tenant`
    # (tenant_lookup='asset__tenant'); the boundary is enforced by the manager's
    # filter_by_tenant() re-applied in BaseViewSet.get_queryset.
    permission_classes = [TokenPermissions, StrictTenantPermission]
    queryset = AssetReservation.objects.select_related(
        "asset",
        "asset__asset_type__manufacturer",
        "asset__tenant",
        "reserved_for",
        "created_by",
    )
    serializer_class = AssetReservationSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AssetReservationFilterSet

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
