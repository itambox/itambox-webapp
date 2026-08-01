import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import exceptions, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.managers import set_current_tenant
from itambox.middleware import set_current_user
from organization.models import Membership, Tenant
from users.api.scim.provider_authentication import SCIMProviderBearerTokenAuthentication
from users.api.scim.provider_patch import (
    SCIMPatchError,
    get_patch_operations,
    parse_group_patch_operations,
    parse_member_ids,
    parse_user_patch_operations,
    parse_user_resource,
    require_object_document,
    validate_display_name,
)
from users.api.scim.provider_services import (
    apply_provider_group_patch,
    apply_provider_user_patch,
    create_provider_group,
    ensure_provider_group_name_available,
    save_provider_group,
    sync_provider_group_members,
)
from users.api.scim.serializers import SCIMGroupSerializer, SCIMServiceProviderConfigSerializer, SCIMUserSerializer
from users.models import UserGroup

User = get_user_model()


class SCIMProviderMixin:
    authentication_classes = [SCIMProviderBearerTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def require_group_permission(self, request, permission):
        """Enforce the method-specific UserGroup permission in the provider tenant."""
        if not request.user.has_perm(permission, obj=self.tenant):
            raise exceptions.PermissionDenied(f"{permission} is required for this provider SCIM group operation.")

    def handle_exception(self, exc):
        from django.core.exceptions import FieldError as DjangoFieldError
        from django.core.exceptions import ValidationError as DjangoValidationError

        scim_type = getattr(exc, "scim_type", None)
        scim_status = getattr(exc, "status_code", status.HTTP_400_BAD_REQUEST)
        if isinstance(exc, SCIMPatchError):
            exc = exceptions.APIException(detail=str(exc))
            exc.status_code = scim_status
        elif isinstance(exc, DjangoValidationError):
            exc = exceptions.ValidationError(detail=exc.message_dict if hasattr(exc, "message_dict") else exc.messages)
        elif isinstance(exc, DjangoFieldError):
            exc = exceptions.ValidationError(detail=str(exc))

        response = super().handle_exception(exc)
        if response is not None:
            detail = response.data.get("detail") if isinstance(response.data, dict) else str(response.data)
            response.data = {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": str(response.status_code),
                "detail": detail,
            }
            if scim_type is not None:
                response.data["scimType"] = scim_type
        return response

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        provider_slug = self.kwargs.get("provider_slug")
        if not provider_slug:
            raise exceptions.ValidationError("provider_slug is required")

        # A provider is a Tenant with is_provider=True; the /api/providers/<slug>/
        # mount is kept but resolves against the tenant tree. _base_manager: no tenant
        # context exists yet, so the tenant-scoped default manager would return nothing.
        self.tenant = Tenant._base_manager.filter(
            is_provider=True,
            deleted_at__isnull=True,
            slug=provider_slug,
        ).first()
        if self.tenant is None:
            raise exceptions.NotFound("Provider not found.")

        # Bind the managing tenant as the current tenant (it IS a tenant now) and the
        # token's owner as the current user so SCIM-driven changelog rows are attributed
        # to the acting service account rather than 'System' (CurrentUserMiddleware
        # captured AnonymousUser before DRF auth ran).
        set_current_tenant(self.tenant)
        if getattr(request, "user", None) and request.user.is_authenticated:
            set_current_user(request.user)
        # Wire request-id so SCIM mutations create ObjectChange records (WP-18).
        # inline import: app-registry: avoid AppRegistryNotReady at module-load time
        from itambox.middleware import _request_id

        _request_id.set(str(uuid.uuid4()))


class ProviderServiceProviderConfigView(SCIMProviderMixin, APIView):
    def get(self, request, *args, **kwargs):
        config_data = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
            "patch": {"supported": True},
            "bulk": {"supported": False, "maxOperations": 1000, "maxPayloadSize": 1048576},
            "filter": {"supported": True, "maxResults": 200},
            "changePassword": {"supported": False},
            "sort": {"supported": False},
            "etag": {"supported": False},
            "authenticationSchemes": [
                {
                    "name": "OAuth Bearer Token",
                    "description": "External identity provisioning via Bearer Token",
                    "specUri": "http://tools.ietf.org/html/rfc6750",
                    "type": "oauthbearertoken",
                    "primary": True,
                },
            ],
        }
        serializer = SCIMServiceProviderConfigSerializer(data=config_data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class SCIMProviderUserListView(SCIMProviderMixin, APIView):
    def get(self, request, *args, **kwargs):
        queryset = User.objects.filter(
            memberships__tenant=self.tenant,
        ).distinct()

        try:
            start_index = int(request.query_params.get("startIndex", 1))
        except ValueError:
            start_index = 1
        try:
            count = int(request.query_params.get("count", 50))
        except ValueError:
            count = 50
        count = min(count, 200)  # Enforce maxResults upper bound

        if start_index < 1:
            start_index = 1

        total_results = queryset.count()
        sliced_queryset = queryset[start_index - 1 : start_index - 1 + count]

        serializer = SCIMUserSerializer(
            sliced_queryset,
            many=True,
            context={
                "request": request,
                "tenant_slug": self.tenant.slug,
                "tenant": self.tenant,
                "scim_base_path": f"/api/providers/{self.tenant.slug}/scim/v2",
            },
        )

        return Response(
            {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
                "totalResults": total_results,
                "itemsPerPage": len(serializer.data),
                "startIndex": start_index,
                "Resources": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request, *args, **kwargs):
        document = require_object_document(request.data)
        patch = parse_user_resource(document)
        username = patch.username
        email = patch.email
        first_name = patch.first_name
        last_name = patch.last_name
        active = patch.active

        user = User.objects.filter(username=username).first()
        if user:
            existing = Membership.objects.filter(user=user, tenant=self.tenant).first()
            if existing:
                return Response(
                    {
                        "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                        "status": "409",
                        "detail": "User already exists in this provider",
                    },
                    status=status.HTTP_409_CONFLICT,
                )

            with transaction.atomic():
                # SCIM provisions identity only: a bare membership at the managing
                # tenant with NO RoleGrant rows — zero permissions and zero reach
                # until granted in-app. Were a
                # provisioning config ever to map roles, it would resolve them against
                # this tenant's own roles and create grants with granted_by=None —
                # SCIM is trusted operator configuration, deliberately unguarded.
                Membership.objects.create(user=user, tenant=self.tenant, is_active=active)
        else:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=username, email=email, first_name=first_name, last_name=last_name, is_active=active
                )
                user.set_unusable_password()
                user.save()

                # See comment above: bare membership, assignments granted in-app.
                Membership.objects.create(user=user, tenant=self.tenant, is_active=active)

        serializer = SCIMUserSerializer(
            user,
            context={
                "request": request,
                "tenant_slug": self.tenant.slug,
                "tenant": self.tenant,
                "scim_base_path": f"/api/providers/{self.tenant.slug}/scim/v2",
            },
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class SCIMProviderUserDetailView(SCIMProviderMixin, APIView):
    def _staff_queryset(self):
        return User.objects.filter(memberships__tenant=self.tenant).distinct()

    def get(self, request, pk, *args, **kwargs):
        user = get_object_or_404(self._staff_queryset(), id=pk)
        serializer = SCIMUserSerializer(
            user,
            context={
                "request": request,
                "tenant_slug": self.tenant.slug,
                "tenant": self.tenant,
                "scim_base_path": f"/api/providers/{self.tenant.slug}/scim/v2",
            },
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request, pk, *args, **kwargs):
        document = require_object_document(request.data)
        user = get_object_or_404(self._staff_queryset(), id=pk)
        patch = parse_user_resource(document)
        user = apply_provider_user_patch(user, self.tenant, patch, actor=request.user)

        serializer = SCIMUserSerializer(
            user,
            context={
                "request": request,
                "tenant_slug": self.tenant.slug,
                "tenant": self.tenant,
                "scim_base_path": f"/api/providers/{self.tenant.slug}/scim/v2",
            },
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, pk, *args, **kwargs):
        user = get_object_or_404(self._staff_queryset(), id=pk)

        patch = parse_user_patch_operations(get_patch_operations(request.data))
        user = apply_provider_user_patch(user, self.tenant, patch, actor=request.user)

        serializer = SCIMUserSerializer(
            user,
            context={
                "request": request,
                "tenant_slug": self.tenant.slug,
                "tenant": self.tenant,
                "scim_base_path": f"/api/providers/{self.tenant.slug}/scim/v2",
            },
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, pk, *args, **kwargs):
        user = get_object_or_404(self._staff_queryset(), id=pk)
        with transaction.atomic():
            # Remove only the membership at this managing tenant. Delete per-instance so
            # each removal is change-logged (QuerySet.delete() bypasses
            # ChangeLoggingMixin). Deleting the membership CASCADEs its RoleGrant
            # rows, revoking own AND managed reach in one stroke.
            for membership in Membership.objects.filter(user=user, tenant=self.tenant):
                membership.delete()
            # Fully de-provisioned user (no memberships anywhere): deactivate the
            # account instead of hard-deleting (same rule as the tenant SCIM path).
            if not Membership.objects.filter(user=user).exists():
                user.is_active = False
                user.save(update_fields=["is_active"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class SCIMProviderGroupListView(SCIMProviderMixin, APIView):
    def get(self, request, *args, **kwargs):
        self.require_group_permission(request, "users.view_usergroup")
        queryset = UserGroup.objects.filter(tenant=self.tenant)

        try:
            start_index = int(request.query_params.get("startIndex", 1))
        except ValueError:
            start_index = 1
        try:
            count = int(request.query_params.get("count", 50))
        except ValueError:
            count = 50
        count = min(count, 200)  # Enforce maxResults upper bound

        if start_index < 1:
            start_index = 1

        total_results = queryset.count()
        sliced_queryset = queryset[start_index - 1 : start_index - 1 + count]

        serializer = SCIMGroupSerializer(
            sliced_queryset,
            many=True,
            context={
                "request": request,
                "tenant_slug": self.tenant.slug,
                "scim_base_path": f"/api/providers/{self.tenant.slug}/scim/v2",
            },
        )

        return Response(
            {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
                "totalResults": total_results,
                "itemsPerPage": len(serializer.data),
                "startIndex": start_index,
                "Resources": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request, *args, **kwargs):
        self.require_group_permission(request, "users.add_usergroup")
        document = require_object_document(request.data)
        name = document.get("displayName")
        if not name:
            return Response(
                {
                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                    "status": "400",
                    "detail": "displayName is required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        name = validate_display_name(name)
        ensure_provider_group_name_available(self.tenant, name)
        member_ids = set(parse_member_ids(document.get("members", [])))

        group = create_provider_group(self.tenant, name, member_ids, actor=request.user)

        serializer = SCIMGroupSerializer(
            group,
            context={
                "request": request,
                "tenant_slug": self.tenant.slug,
                "scim_base_path": f"/api/providers/{self.tenant.slug}/scim/v2",
            },
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class SCIMProviderGroupDetailView(SCIMProviderMixin, APIView):
    def get(self, request, pk, *args, **kwargs):
        self.require_group_permission(request, "users.view_usergroup")
        group = get_object_or_404(UserGroup.objects.filter(tenant=self.tenant), id=pk)
        serializer = SCIMGroupSerializer(
            group,
            context={
                "request": request,
                "tenant_slug": self.tenant.slug,
                "scim_base_path": f"/api/providers/{self.tenant.slug}/scim/v2",
            },
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request, pk, *args, **kwargs):
        self.require_group_permission(request, "users.change_usergroup")
        document = require_object_document(request.data)
        name = document.get("displayName")
        if not name:
            return Response(
                {
                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                    "status": "400",
                    "detail": "displayName is required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        name = validate_display_name(name)
        member_ids = set(parse_member_ids(document.get("members", [])))

        with transaction.atomic():
            group = get_object_or_404(
                UserGroup.objects.select_for_update().filter(tenant=self.tenant),
                id=pk,
            )
            ensure_provider_group_name_available(self.tenant, name, exclude_pk=group.pk)
            group.name = name
            save_provider_group(group, self.tenant, actor=request.user)
            sync_provider_group_members(
                self.tenant,
                group,
                member_ids,
                actor=request.user,
            )

        serializer = SCIMGroupSerializer(
            group,
            context={
                "request": request,
                "tenant_slug": self.tenant.slug,
                "scim_base_path": f"/api/providers/{self.tenant.slug}/scim/v2",
            },
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, pk, *args, **kwargs):
        self.require_group_permission(request, "users.change_usergroup")
        group = get_object_or_404(UserGroup.objects.filter(tenant=self.tenant), id=pk)

        patch = parse_group_patch_operations(get_patch_operations(request.data))
        group = apply_provider_group_patch(
            self.tenant,
            group,
            patch,
            actor=request.user,
        )

        serializer = SCIMGroupSerializer(
            group,
            context={
                "request": request,
                "tenant_slug": self.tenant.slug,
                "scim_base_path": f"/api/providers/{self.tenant.slug}/scim/v2",
            },
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, pk, *args, **kwargs):
        self.require_group_permission(request, "users.delete_usergroup")
        group = get_object_or_404(UserGroup.objects.filter(tenant=self.tenant), id=pk)
        with transaction.atomic():
            # Soft-delete via the model's delete() for change-logging.
            group.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
