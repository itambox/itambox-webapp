"""OpenAPI descriptions for the tenant and provider SCIM contracts.

The SCIM views intentionally remain APIViews because their runtime behavior is
not DRF generic CRUD. These helpers keep the wire contract explicit without
changing authentication, request parsing, or response behavior.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema

from .serializers import (
    SCIMErrorSerializer,
    SCIMGroupListResponseSerializer,
    SCIMGroupRequestSerializer,
    SCIMGroupSerializer,
    SCIMPatchRequestSerializer,
    SCIMServiceProviderConfigSerializer,
    SCIMUserListResponseSerializer,
    SCIMUserRequestSerializer,
    SCIMUserSerializer,
)

SCIM_ERROR_RESPONSES = {
    400: OpenApiResponse(SCIMErrorSerializer, description="The SCIM request is invalid."),
    401: OpenApiResponse(SCIMErrorSerializer, description="Bearer authentication failed."),
    403: OpenApiResponse(SCIMErrorSerializer, description="The authenticated principal is not allowed."),
    404: OpenApiResponse(SCIMErrorSerializer, description="The SCIM resource was not found."),
    409: OpenApiResponse(SCIMErrorSerializer, description="The request conflicts with an existing SCIM resource."),
}

SCIM_TENANT_LIST_PARAMETERS = [
    OpenApiParameter("tenant_slug", OpenApiTypes.STR, OpenApiParameter.PATH, description="Tenant URL slug."),
    OpenApiParameter("filter", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False),
    OpenApiParameter("startIndex", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, default=1),
    OpenApiParameter("count", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, default=50),
]
SCIM_PROVIDER_LIST_PARAMETERS = [
    OpenApiParameter("provider_slug", OpenApiTypes.STR, OpenApiParameter.PATH, description="Provider URL slug."),
    OpenApiParameter("filter", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False),
    OpenApiParameter("startIndex", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, default=1),
    OpenApiParameter("count", OpenApiTypes.INT, OpenApiParameter.QUERY, required=False, default=50),
]
SCIM_TENANT_DETAIL_PARAMETERS = [
    *SCIM_TENANT_LIST_PARAMETERS[:1],
    OpenApiParameter("id", OpenApiTypes.UUID, OpenApiParameter.PATH, description="SCIM resource identifier."),
]
SCIM_PROVIDER_DETAIL_PARAMETERS = [
    *SCIM_PROVIDER_LIST_PARAMETERS[:1],
    OpenApiParameter("id", OpenApiTypes.UUID, OpenApiParameter.PATH, description="SCIM resource identifier."),
]


def scim_operation(
    operation_id,
    *,
    parameters=(),
    response=None,
    response_status=200,
    request=None,
    tags=(),
    extra_responses=None,
):
    """Build a complete SCIM operation description without touching runtime code."""
    responses = dict(SCIM_ERROR_RESPONSES)
    if response is not None or response_status == 204:
        responses[response_status] = (
            OpenApiResponse(response, description="SCIM operation succeeded.")
            if response_status != 204
            else OpenApiResponse(description="SCIM resource deleted.")
        )
    if extra_responses:
        responses.update(extra_responses)
    return extend_schema(
        operation_id=operation_id,
        tags=list(tags),
        parameters=list(parameters),
        request=request,
        responses=responses,
    )


SCIM_TENANT_SERVICE_PROVIDER_CONFIG = scim_operation(
    "scim_tenant_service_provider_config_retrieve",
    parameters=SCIM_TENANT_LIST_PARAMETERS[:1],
    response=SCIMServiceProviderConfigSerializer,
    tags=("SCIM Tenant",),
)
SCIM_PROVIDER_SERVICE_PROVIDER_CONFIG = scim_operation(
    "scim_provider_service_provider_config_retrieve",
    parameters=SCIM_PROVIDER_LIST_PARAMETERS[:1],
    response=SCIMServiceProviderConfigSerializer,
    tags=("SCIM Provider",),
)

SCIM_TENANT_USER_LIST = scim_operation(
    "scim_tenant_users_list",
    parameters=SCIM_TENANT_LIST_PARAMETERS,
    response=SCIMUserListResponseSerializer,
    tags=("SCIM Tenant",),
)
SCIM_TENANT_USER_CREATE = scim_operation(
    "scim_tenant_users_create",
    parameters=SCIM_TENANT_LIST_PARAMETERS[:1],
    response=SCIMUserSerializer,
    request=SCIMUserRequestSerializer,
    response_status=201,
    extra_responses={200: OpenApiResponse(SCIMUserSerializer, description="An existing SCIM user was correlated.")},
    tags=("SCIM Tenant",),
)
SCIM_TENANT_USER_DETAIL = scim_operation(
    "scim_tenant_user_retrieve",
    parameters=SCIM_TENANT_DETAIL_PARAMETERS,
    response=SCIMUserSerializer,
    tags=("SCIM Tenant",),
)
SCIM_TENANT_USER_REPLACE = scim_operation(
    "scim_tenant_user_replace",
    parameters=SCIM_TENANT_DETAIL_PARAMETERS,
    response=SCIMUserSerializer,
    request=SCIMUserRequestSerializer,
    tags=("SCIM Tenant",),
)
SCIM_TENANT_USER_UPDATE = scim_operation(
    "scim_tenant_user_update",
    parameters=SCIM_TENANT_DETAIL_PARAMETERS,
    response=SCIMUserSerializer,
    request=SCIMPatchRequestSerializer,
    tags=("SCIM Tenant",),
)
SCIM_TENANT_USER_DELETE = scim_operation(
    "scim_tenant_user_delete",
    parameters=SCIM_TENANT_DETAIL_PARAMETERS,
    response_status=204,
    tags=("SCIM Tenant",),
)
SCIM_TENANT_GROUP_LIST = scim_operation(
    "scim_tenant_groups_list",
    parameters=SCIM_TENANT_LIST_PARAMETERS,
    response=SCIMGroupListResponseSerializer,
    tags=("SCIM Tenant",),
)
SCIM_TENANT_GROUP_CREATE = scim_operation(
    "scim_tenant_groups_create",
    parameters=SCIM_TENANT_LIST_PARAMETERS[:1],
    tags=("SCIM Tenant",),
    extra_responses={403: OpenApiResponse(SCIMErrorSerializer, description="Tenant SCIM groups are read-only.")},
)
SCIM_TENANT_GROUP_DETAIL = scim_operation(
    "scim_tenant_group_retrieve",
    parameters=SCIM_TENANT_DETAIL_PARAMETERS,
    response=SCIMGroupSerializer,
    tags=("SCIM Tenant",),
)
SCIM_TENANT_GROUP_REPLACE = scim_operation(
    "scim_tenant_group_replace",
    parameters=SCIM_TENANT_DETAIL_PARAMETERS,
    tags=("SCIM Tenant",),
    extra_responses={403: OpenApiResponse(SCIMErrorSerializer, description="Tenant SCIM groups are read-only.")},
)
SCIM_TENANT_GROUP_UPDATE = scim_operation(
    "scim_tenant_group_update",
    parameters=SCIM_TENANT_DETAIL_PARAMETERS,
    request=SCIMPatchRequestSerializer,
    tags=("SCIM Tenant",),
    extra_responses={403: OpenApiResponse(SCIMErrorSerializer, description="Tenant SCIM groups are read-only.")},
)
SCIM_TENANT_GROUP_DELETE = scim_operation(
    "scim_tenant_group_delete",
    parameters=SCIM_TENANT_DETAIL_PARAMETERS,
    tags=("SCIM Tenant",),
    extra_responses={403: OpenApiResponse(SCIMErrorSerializer, description="Tenant SCIM groups are read-only.")},
)

SCIM_PROVIDER_USER_LIST = scim_operation(
    "scim_provider_users_list",
    parameters=SCIM_PROVIDER_LIST_PARAMETERS,
    response=SCIMUserListResponseSerializer,
    tags=("SCIM Provider",),
)
SCIM_PROVIDER_USER_CREATE = scim_operation(
    "scim_provider_users_create",
    parameters=SCIM_PROVIDER_LIST_PARAMETERS[:1],
    response=SCIMUserSerializer,
    request=SCIMUserRequestSerializer,
    response_status=201,
    extra_responses={200: OpenApiResponse(SCIMUserSerializer, description="An existing SCIM user was correlated.")},
    tags=("SCIM Provider",),
)
SCIM_PROVIDER_USER_DETAIL = scim_operation(
    "scim_provider_user_retrieve",
    parameters=SCIM_PROVIDER_DETAIL_PARAMETERS,
    response=SCIMUserSerializer,
    tags=("SCIM Provider",),
)
SCIM_PROVIDER_USER_REPLACE = scim_operation(
    "scim_provider_user_replace",
    parameters=SCIM_PROVIDER_DETAIL_PARAMETERS,
    response=SCIMUserSerializer,
    request=SCIMUserRequestSerializer,
    tags=("SCIM Provider",),
)
SCIM_PROVIDER_USER_UPDATE = scim_operation(
    "scim_provider_user_update",
    parameters=SCIM_PROVIDER_DETAIL_PARAMETERS,
    response=SCIMUserSerializer,
    request=SCIMPatchRequestSerializer,
    tags=("SCIM Provider",),
)
SCIM_PROVIDER_USER_DELETE = scim_operation(
    "scim_provider_user_delete",
    parameters=SCIM_PROVIDER_DETAIL_PARAMETERS,
    response_status=204,
    tags=("SCIM Provider",),
)
SCIM_PROVIDER_GROUP_LIST = scim_operation(
    "scim_provider_groups_list",
    parameters=SCIM_PROVIDER_LIST_PARAMETERS,
    response=SCIMGroupListResponseSerializer,
    tags=("SCIM Provider",),
)
SCIM_PROVIDER_GROUP_CREATE = scim_operation(
    "scim_provider_groups_create",
    parameters=SCIM_PROVIDER_LIST_PARAMETERS[:1],
    response=SCIMGroupSerializer,
    request=SCIMGroupRequestSerializer,
    response_status=201,
    extra_responses={200: OpenApiResponse(SCIMGroupSerializer, description="An existing SCIM group was correlated.")},
    tags=("SCIM Provider",),
)
SCIM_PROVIDER_GROUP_DETAIL = scim_operation(
    "scim_provider_group_retrieve",
    parameters=SCIM_PROVIDER_DETAIL_PARAMETERS,
    response=SCIMGroupSerializer,
    tags=("SCIM Provider",),
)
SCIM_PROVIDER_GROUP_REPLACE = scim_operation(
    "scim_provider_group_replace",
    parameters=SCIM_PROVIDER_DETAIL_PARAMETERS,
    response=SCIMGroupSerializer,
    request=SCIMGroupRequestSerializer,
    tags=("SCIM Provider",),
)
SCIM_PROVIDER_GROUP_UPDATE = scim_operation(
    "scim_provider_group_update",
    parameters=SCIM_PROVIDER_DETAIL_PARAMETERS,
    response=SCIMGroupSerializer,
    request=SCIMPatchRequestSerializer,
    tags=("SCIM Provider",),
)
SCIM_PROVIDER_GROUP_DELETE = scim_operation(
    "scim_provider_group_delete",
    parameters=SCIM_PROVIDER_DETAIL_PARAMETERS,
    response_status=204,
    tags=("SCIM Provider",),
)
