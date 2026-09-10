import graphene
from django.core.exceptions import ValidationError
from django.db.models import Q
from graphene_django import DjangoObjectType
from graphql import GraphQLError

from assets.services.specifications._command_support import load_prospective_definition
from core.graphql_utils import check_permission, get_object_or_denied, paginate_queryset
from organization.models import Location, Tenant

from .graphql_specifications.inputs import RequestedScopeSelectorInput
from .graphql_specifications.integration import (
    asset_queryset_for_scope,
    asset_type_connection,
    authenticated_user,
    bind_scope,
    category_fieldsets_for,
    choice_set_for_identity,
    decode_cursor,
    fieldsets_for_type,
    has_global_permission,
    library_origin_for,
    owner_resource_revision,
    owner_user_errors,
    page_size,
    prepare_asset_graph,
    prepare_type_graph,
    require_global_permission,
    resolve_read_scope,
    specification_field_connection,
)
from .graphql_specifications.loaders import request_loader_for_info
from .graphql_specifications.mutations import (
    AddChoice,
    ApplyCategoryDefaults,
    ApplyLibrary,
    CleanupSpecificationHistory,
    CreateAssetType,
    CreateChoiceSet,
    CreateSpecificationField,
    CreateSpecificationFieldset,
    ExportLibrary,
    PreviewApplyCategoryDefaults,
    PreviewAssetTypeComposition,
    PreviewAssetTypeCreate,
    PreviewLibrary,
    PreviewSpecificationHistoryCleanup,
    ReorderChoices,
    SetAssetTypeComposition,
    SetCategoryDefaults,
    UpdateAssetSpecifications,
    UpdateAssetTypeSpecifications,
    UpdateChoice,
    UpdateChoiceSet,
    UpdateSpecificationFieldPolicy,
    UpdateSpecificationFieldset,
)
from .graphql_specifications.scalars import CursorScalar
from .graphql_specifications.types import (
    ChoiceSetType,
    LibraryOriginType,
    PageInfoType,
    SpecificationDefinitionType,
    SpecificationEntryType,
    SpecificationFieldConnectionType,
    SpecificationFieldsetType,
    SpecificationTargetEnum,
    UserErrorType,
)
from .models import Asset, AssetRole, AssetType, Category, Depreciation, Manufacturer, StatusLabel, Supplier

_SCHEMA_MISSING = object()


def _apply_native_asset_type_create(asset, kwargs, *, user, tenant):
    if "asset_type_id" not in kwargs:
        return
    asset_type_id = kwargs.pop("asset_type_id")
    if asset_type_id is None:
        raise GraphQLError(
            "Asset Type assignment must use the typed specification command.",
            extensions={"code": "INVALID_TYPE", "path": ["assetTypeId"]},
        )
    asset.asset_type = get_object_or_denied(AssetType, asset_type_id, user, tenant=tenant)


def _apply_asset_relation_updates(asset, kwargs, *, user, tenant):
    for key, model, attribute in (
        ("asset_role_id", AssetRole, "asset_role"),
        ("status_id", StatusLabel, "status"),
        ("location_id", Location, "location"),
        ("supplier_id", Supplier, "supplier"),
    ):
        if key in kwargs:
            setattr(
                asset,
                attribute,
                get_object_or_denied(model, kwargs.pop(key), user, tenant=tenant),
            )


class TenantNode(DjangoObjectType):
    class Meta:
        model = Tenant
        fields = ("id", "name", "slug")


class LocationNode(DjangoObjectType):
    class Meta:
        model = Location
        fields = ("id", "name", "slug", "site", "tenant")


class StatusLabelNode(DjangoObjectType):
    class Meta:
        model = StatusLabel
        fields = ("id", "name", "slug", "type", "description", "color", "created_at", "updated_at")


class AssetRoleNode(DjangoObjectType):
    class Meta:
        model = AssetRole
        fields = ("id", "name", "slug", "description", "color", "created_at", "updated_at")


class ManufacturerNode(DjangoObjectType):
    class Meta:
        model = Manufacturer
        fields = ("id", "name", "slug", "description", "created_at", "updated_at", "software_products")


class DepreciationNode(DjangoObjectType):
    class Meta:
        model = Depreciation
        fields = ("id", "name", "months", "created_at", "updated_at")


class AssetTypeNode(DjangoObjectType):
    resource_revision = graphene.String(required=True)
    fieldsets = graphene.List(graphene.NonNull(SpecificationFieldsetType), required=True)
    specification_definition = graphene.Field(
        SpecificationDefinitionType,
        target=SpecificationTargetEnum(required=True),
        required=True,
    )
    specification_entries = graphene.List(graphene.NonNull(SpecificationEntryType), required=True)
    specification_issues = graphene.List(graphene.NonNull(UserErrorType), required=True)
    library = graphene.Field(LibraryOriginType)

    class Meta:
        model = AssetType
        name = "AssetType"
        fields = (
            "id",
            "slug",
            "manufacturer",
            "model",
            "part_number",
            "eol_months",
            "depreciation",
            "category",
            "asset_role",
            "description",
            "requestable",
            "created_at",
            "updated_at",
        )

    @staticmethod
    def resolve_resource_revision(root, info):
        del info
        return owner_resource_revision(root)

    @staticmethod
    def resolve_fieldsets(root, info):
        loader = request_loader_for_info(info)
        return fieldsets_for_type(loader, int(root.pk))

    @staticmethod
    def resolve_specification_definition(root, info, target):
        loader = request_loader_for_info(info)
        target_kind = getattr(target, "value", target)
        return loader.definition_for_type(int(root.pk), target_kind=target_kind)

    @staticmethod
    def resolve_specification_entries(root, info):
        loader = request_loader_for_info(info)
        return loader.read_owner(root, target_kind="asset_type").projection.entries

    @staticmethod
    def resolve_specification_issues(root, info):
        loader = request_loader_for_info(info)
        return owner_user_errors(loader, root, "asset_type")

    @staticmethod
    def resolve_library(root, info):
        del info
        return library_origin_for(root)


class SupplierNode(DjangoObjectType):
    class Meta:
        model = Supplier
        fields = ("id", "name", "slug", "website", "address", "notes", "created_at", "updated_at")


class CategoryNode(DjangoObjectType):
    key = graphene.String(required=True)
    resource_revision = graphene.String(required=True)
    default_fieldsets = graphene.List(graphene.NonNull(SpecificationFieldsetType), required=True)

    class Meta:
        model = Category
        name = "Category"
        fields = ("id", "name", "slug", "color", "description", "applies_to", "created_at", "updated_at")

    @staticmethod
    def resolve_key(root, info):
        del info
        return root.slug

    @staticmethod
    def resolve_resource_revision(root, info):
        del info
        return owner_resource_revision(root)

    @staticmethod
    def resolve_default_fieldsets(root, info):
        del info
        return category_fieldsets_for(root)


class AssetNode(DjangoObjectType):
    resource_revision = graphene.String(required=True)
    specification_definition = graphene.Field(SpecificationDefinitionType, required=True)
    specification_entries = graphene.List(graphene.NonNull(SpecificationEntryType), required=True)
    specification_issues = graphene.List(graphene.NonNull(UserErrorType), required=True)

    class Meta:
        model = Asset
        name = "Asset"
        fields = (
            "id",
            "name",
            "asset_tag",
            "serial_number",
            "asset_type",
            "asset_role",
            "status",
            "location",
            "tenant",
            "purchase_date",
            "supplier",
            "order_number",
            "requestable",
            "created_at",
            "updated_at",
        )

    @staticmethod
    def resolve_asset_type(root, info):
        if not has_global_permission(info, "assets.view_assettype"):
            return None
        return root.asset_type

    @staticmethod
    def resolve_resource_revision(root, info):
        del info
        return owner_resource_revision(root)

    @staticmethod
    def resolve_specification_definition(root, info):
        loader = request_loader_for_info(info)
        return loader.read_owner(root, target_kind="asset").definition

    @staticmethod
    def resolve_specification_entries(root, info):
        loader = request_loader_for_info(info)
        return loader.read_owner(root, target_kind="asset").projection.entries

    @staticmethod
    def resolve_specification_issues(root, info):
        loader = request_loader_for_info(info)
        return owner_user_errors(loader, root, "asset")


ASSET_SORTABLE_FIELDS = {
    "name",
    "-name",
    "asset_tag",
    "-asset_tag",
    "serial_number",
    "-serial_number",
    "purchase_date",
    "-purchase_date",
    "created_at",
    "-created_at",
    "updated_at",
    "-updated_at",
}


class AssetTypeEdgeType(graphene.ObjectType):
    class Meta:
        name = "AssetTypeEdge"

    cursor = graphene.Field(CursorScalar, required=True)
    node = graphene.Field(lambda: AssetTypeNode, required=True)


class AssetTypeConnectionType(graphene.ObjectType):
    class Meta:
        name = "AssetTypeConnection"

    edges = graphene.List(graphene.NonNull(AssetTypeEdgeType), required=True)
    page_info = graphene.Field(PageInfoType, required=True)


class Query(graphene.ObjectType):
    assets = graphene.List(
        AssetNode,
        requested_scope=RequestedScopeSelectorInput(required=True),
        limit=graphene.Int(),
        offset=graphene.Int(),
        sort_by=graphene.String(),
        name=graphene.String(),
        asset_tag=graphene.String(),
        serial_number=graphene.String(),
        status_id=graphene.ID(),
        location_id=graphene.ID(),
    )
    asset = graphene.Field(
        AssetNode,
        id=graphene.ID(required=True),
        requested_scope=RequestedScopeSelectorInput(required=True),
    )
    asset_type = graphene.Field(AssetTypeNode, id=graphene.ID(required=True))
    asset_types = graphene.Field(
        AssetTypeConnectionType,
        first=graphene.Int(required=True, default_value=50),
        after=CursorScalar(),
        required=True,
    )
    specification_fields = graphene.Field(
        SpecificationFieldConnectionType,
        first=graphene.Int(required=True, default_value=50),
        after=CursorScalar(),
        required=True,
    )
    choice_set = graphene.Field(ChoiceSetType, identity=graphene.String(required=True))
    category = graphene.Field(CategoryNode, id=graphene.ID(required=True))
    preview_asset_type_definition = graphene.Field(
        SpecificationDefinitionType,
        category_id=graphene.ID(),
        fieldsets=graphene.List(graphene.NonNull(graphene.String)),
        target=SpecificationTargetEnum(required=True),
        required=True,
    )

    def resolve_assets(self, info, requested_scope, limit=None, offset=None, sort_by=None, **kwargs):
        authenticated_user(info)
        scope = resolve_read_scope(info, requested_scope)
        if scope is None:
            return []
        loader = request_loader_for_info(info)
        bind_scope(loader, scope)
        qs = asset_queryset_for_scope(scope)
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        if sort_by and sort_by in ASSET_SORTABLE_FIELDS:
            qs = qs.order_by(sort_by)
        items = list(paginate_queryset(qs, limit, offset))
        prepare_asset_graph(loader, items)
        return items

    def resolve_asset(self, info, id, requested_scope):
        authenticated_user(info)
        scope = resolve_read_scope(info, requested_scope)
        if scope is None:
            return None
        loader = request_loader_for_info(info)
        bind_scope(loader, scope)
        try:
            asset = asset_queryset_for_scope(scope).get(pk=id)
            prepare_asset_graph(loader, (asset,))
            return asset
        except Asset.DoesNotExist:
            return None

    def resolve_asset_type(self, info, id):
        require_global_permission(info, "assets.view_assettype")
        try:
            asset_type = AssetType.objects.select_related("library", "library__accepted_release").get(pk=id)
        except AssetType.DoesNotExist:
            return None
        prepare_type_graph(request_loader_for_info(info), (asset_type,))
        return asset_type

    def resolve_asset_types(self, info, first=50, after=None):
        require_global_permission(info, "assets.view_assettype")
        size = page_size(first)
        asset_types_qs = AssetType.objects.select_related("library", "library__accepted_release").order_by("slug", "pk")
        if after:
            after_slug, after_id = decode_cursor(after, prefix="asset-type")
            asset_types_qs = asset_types_qs.filter(Q(slug__gt=after_slug) | Q(slug=after_slug, pk__gt=after_id))
        asset_types = tuple(asset_types_qs[: size + 1])
        loader = request_loader_for_info(info)
        prepare_type_graph(loader, asset_types)
        return asset_type_connection(asset_types, first=size, after=None)

    def resolve_specification_fields(self, info, first=50, after=None):
        require_global_permission(info, "extras.view_customfield")
        loader = request_loader_for_info(info)
        graph = loader.global_graph(("asset_type", "asset"))
        fields = tuple(graph.fields_by_key.values())
        return specification_field_connection(fields, first=first, after=after)

    def resolve_choice_set(self, info, identity):
        require_global_permission(info, "extras.view_customfieldchoiceset")
        return choice_set_for_identity(identity, loader=request_loader_for_info(info))

    def resolve_category(self, info, id):
        require_global_permission(info, "assets.view_category")
        return Category.objects.filter(pk=id).first()

    def resolve_preview_asset_type_definition(
        self,
        info,
        target,
        category_id=None,
        fieldsets=_SCHEMA_MISSING,
    ):
        require_global_permission(info, "assets.view_assettype")
        target_kind = getattr(target, "value", target)
        if target_kind not in {"asset_type", "asset"}:
            raise GraphQLError(
                "The submitted value has an invalid type.",
                extensions={"code": "INVALID_TYPE", "path": ["target"]},
            )
        category = None
        if category_id is not None:
            category = Category.objects.filter(pk=category_id).first()
            if category is None:
                raise GraphQLError(
                    "The requested object is unavailable.",
                    extensions={"code": "OBJECT_UNAVAILABLE", "path": ["categoryId"]},
                )
        if fieldsets is _SCHEMA_MISSING:
            if category is None:
                selected_fieldsets = ()
            else:
                selected_fieldsets = tuple(str(item.definition.identity) for item in category_fieldsets_for(category))
        elif fieldsets is None:
            raise GraphQLError(
                "The submitted value has an invalid type.",
                extensions={"code": "INVALID_TYPE", "path": ["fieldsets"]},
            )
        else:
            selected_fieldsets = tuple(str(identity) for identity in fieldsets)
        try:
            definition, _, _ = load_prospective_definition(selected_fieldsets, target_kind, ())
        except (KeyError, TypeError, ValueError):
            raise GraphQLError(
                "The requested object is unavailable.",
                extensions={"code": "OBJECT_UNAVAILABLE", "path": ["fieldsets"]},
            ) from None
        return definition


class CreateAsset(graphene.Mutation):
    class Arguments:
        name = graphene.String(required=True)
        asset_tag = graphene.String()
        serial_number = graphene.String()
        asset_type_id = graphene.ID()
        asset_role_id = graphene.ID()
        status_id = graphene.ID()
        location_id = graphene.ID()
        supplier_id = graphene.ID()
        purchase_date = graphene.Date()
        purchase_cost = graphene.Float()
        salvage_value = graphene.Float()
        order_number = graphene.String()
        notes = graphene.String()

    asset = graphene.Field(AssetNode)

    def mutate(self, info, **kwargs):
        user = check_permission(info, "assets.add_asset")
        if "asset_type_id" in kwargs and kwargs["asset_type_id"] is None:
            raise GraphQLError(
                "Asset Type must be an ID when supplied.",
                extensions={"code": "INVALID_TYPE", "path": ["assetTypeId"]},
            )
        active_tenant = getattr(info.context, "active_tenant", None)

        asset = Asset(tenant=active_tenant)
        _apply_native_asset_type_create(asset, kwargs, user=user, tenant=active_tenant)
        _apply_asset_relation_updates(asset, kwargs, user=user, tenant=active_tenant)

        ALLOWED_FIELDS = {
            "name",
            "asset_tag",
            "serial_number",
            "purchase_date",
            "purchase_cost",
            "salvage_value",
            "order_number",
            "notes",
        }
        for key, val in kwargs.items():
            if key in ALLOWED_FIELDS:
                setattr(asset, key, val)

        try:
            asset.full_clean()
        except ValidationError as e:
            raise GraphQLError(
                "Validation failed",
                extensions={"validation_errors": e.message_dict if hasattr(e, "message_dict") else e.messages},
            ) from e
        asset.save()
        return CreateAsset(asset=asset)


class UpdateAsset(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)
        name = graphene.String()
        asset_tag = graphene.String()
        serial_number = graphene.String()
        asset_type_id = graphene.ID()
        asset_role_id = graphene.ID()
        status_id = graphene.ID()
        location_id = graphene.ID()
        supplier_id = graphene.ID()
        purchase_date = graphene.Date()
        purchase_cost = graphene.Float()
        salvage_value = graphene.Float()
        order_number = graphene.String()
        notes = graphene.String()

    asset = graphene.Field(AssetNode)

    def mutate(self, info, id, **kwargs):
        user = check_permission(info, "assets.change_asset")
        if "asset_type_id" in kwargs:
            raise GraphQLError(
                "Asset Type changes must use updateAssetSpecifications.",
                extensions={"code": "INVALID_TYPE", "path": ["assetTypeId"]},
            )
        active_tenant = getattr(info.context, "active_tenant", None)
        asset = get_object_or_denied(Asset, id, user, tenant=active_tenant)
        check_permission(info, "assets.change_asset", obj=asset)

        _apply_asset_relation_updates(asset, kwargs, user=user, tenant=active_tenant)

        ALLOWED_FIELDS = {
            "name",
            "asset_tag",
            "serial_number",
            "purchase_date",
            "purchase_cost",
            "salvage_value",
            "order_number",
            "notes",
        }
        for key, val in kwargs.items():
            if key in ALLOWED_FIELDS:
                setattr(asset, key, val)

        try:
            asset.full_clean()
        except ValidationError as e:
            raise GraphQLError(
                "Validation failed",
                extensions={"validation_errors": e.message_dict if hasattr(e, "message_dict") else e.messages},
            ) from e
        asset.save()
        return UpdateAsset(asset=asset)


class DeleteAsset(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)

    success = graphene.Boolean()

    def mutate(self, info, id):
        user = check_permission(info, "assets.delete_asset")
        active_tenant = getattr(info.context, "active_tenant", None)
        asset = get_object_or_denied(Asset, id, user, tenant=active_tenant)
        check_permission(info, "assets.delete_asset", obj=asset)
        asset.delete()
        return DeleteAsset(success=True)


class Mutation(graphene.ObjectType):
    create_asset = CreateAsset.Field()
    update_asset = UpdateAsset.Field()
    delete_asset = DeleteAsset.Field()
    preview_asset_type_create = PreviewAssetTypeCreate.Field(required=True)
    create_asset_type = CreateAssetType.Field(required=True)
    update_asset_type_specifications = UpdateAssetTypeSpecifications.Field(required=True)
    preview_apply_category_defaults = PreviewApplyCategoryDefaults.Field(required=True)
    apply_category_defaults = ApplyCategoryDefaults.Field(required=True)
    set_asset_type_composition = SetAssetTypeComposition.Field(required=True)
    update_asset_specifications = UpdateAssetSpecifications.Field(required=True)
    set_category_defaults = SetCategoryDefaults.Field(required=True)
    cleanup_specification_history = CleanupSpecificationHistory.Field(required=True)
    preview_specification_history_cleanup = PreviewSpecificationHistoryCleanup.Field(required=True)
    create_specification_field = CreateSpecificationField.Field(required=True)
    update_specification_field_policy = UpdateSpecificationFieldPolicy.Field(required=True)
    create_specification_fieldset = CreateSpecificationFieldset.Field(required=True)
    update_specification_fieldset = UpdateSpecificationFieldset.Field(required=True)
    create_choice_set = CreateChoiceSet.Field(required=True)
    update_choice_set = UpdateChoiceSet.Field(required=True)
    add_choice = AddChoice.Field(required=True)
    update_choice = UpdateChoice.Field(required=True)
    reorder_choices = ReorderChoices.Field(required=True)
    preview_asset_type_composition = PreviewAssetTypeComposition.Field(required=True)
    preview_library = PreviewLibrary.Field(required=True)
    apply_library = ApplyLibrary.Field(required=True)
    export_library = ExportLibrary.Field(required=True)
