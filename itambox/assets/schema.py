import graphene
from graphene_django import DjangoObjectType
from .models import Asset, StatusLabel, AssetRole, Manufacturer, Depreciation, AssetType, Supplier, Category
from organization.models import Location, Tenant
from core.graphql_utils import check_permission, get_object_or_denied
from django.core.exceptions import ValidationError

MAX_PAGINATION_LIMIT = 200

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
    class Meta:
        model = AssetType
        fields = ("id", "slug", "manufacturer", "model", "part_number", "eol_months", "depreciation", "category", "asset_role", "description", "requestable", "created_at", "updated_at")

class SupplierNode(DjangoObjectType):
    class Meta:
        model = Supplier
        fields = ("id", "name", "slug", "website", "contact_email", "contact_phone", "contact_name", "created_at", "updated_at")

class CategoryNode(DjangoObjectType):
    class Meta:
        model = Category
        fields = ("id", "name", "slug", "color", "description", "applies_to", "created_at", "updated_at")

class AssetNode(DjangoObjectType):
    class Meta:
        model = Asset
        fields = ("id", "name", "asset_tag", "serial_number", "asset_type", "asset_role", "status", "location", "tenant", "purchase_date", "warranty_expiration", "supplier", "order_number", "requestable", "created_at", "updated_at")

ASSET_SORTABLE_FIELDS = {"name", "-name", "asset_tag", "-asset_tag", "serial_number", "-serial_number", "purchase_date", "-purchase_date", "warranty_expiration", "-warranty_expiration", "created_at", "-created_at", "updated_at", "-updated_at"}

class Query(graphene.ObjectType):
    assets = graphene.List(
        AssetNode,
        limit=graphene.Int(),
        offset=graphene.Int(),
        sort_by=graphene.String(),
        name=graphene.String(),
        asset_tag=graphene.String(),
        serial_number=graphene.String(),
        status_id=graphene.ID(),
        location_id=graphene.ID(),
    )
    asset = graphene.Field(AssetNode, id=graphene.ID(required=True))

    def resolve_assets(self, info, limit=None, offset=None, sort_by=None, **kwargs):
        check_permission(info, 'assets.view_asset')
        qs = Asset.objects.all()
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        if sort_by and sort_by in ASSET_SORTABLE_FIELDS:
            qs = qs.order_by(sort_by)
        if offset is not None:
            qs = qs[offset:]
        limit = min(limit, MAX_PAGINATION_LIMIT) if limit is not None else MAX_PAGINATION_LIMIT
        qs = qs[:limit]
        return qs

    def resolve_asset(self, info, id):
        check_permission(info, 'assets.view_asset')
        try:
            return Asset.objects.get(pk=id)
        except Asset.DoesNotExist:
            return None

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
        warranty_expiration = graphene.Date()
        purchase_cost = graphene.Float()
        salvage_value = graphene.Float()
        order_number = graphene.String()
        notes = graphene.String()

    asset = graphene.Field(AssetNode)

    def mutate(self, info, **kwargs):
        user = check_permission(info, 'assets.add_asset')
        active_tenant = getattr(info.context, 'active_tenant', None)
        
        asset = Asset(tenant=active_tenant)
        
        if 'asset_type_id' in kwargs:
            asset.asset_type = get_object_or_denied(AssetType, kwargs.pop('asset_type_id'), user)
        if 'asset_role_id' in kwargs:
            asset.asset_role = get_object_or_denied(AssetRole, kwargs.pop('asset_role_id'), user)
        if 'status_id' in kwargs:
            asset.status = get_object_or_denied(StatusLabel, kwargs.pop('status_id'), user)
        if 'location_id' in kwargs:
            asset.location = get_object_or_denied(Location, kwargs.pop('location_id'), user)
        if 'supplier_id' in kwargs:
            asset.supplier = get_object_or_denied(Supplier, kwargs.pop('supplier_id'), user)
            
        for key, val in kwargs.items():
            setattr(asset, key, val)
            
        try:
            asset.full_clean()
        except ValidationError as e:
            raise Exception(str(e.message_dict if hasattr(e, 'message_dict') else e.messages))
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
        warranty_expiration = graphene.Date()
        purchase_cost = graphene.Float()
        salvage_value = graphene.Float()
        order_number = graphene.String()
        notes = graphene.String()

    asset = graphene.Field(AssetNode)

    def mutate(self, info, id, **kwargs):
        user = check_permission(info, 'assets.change_asset')
        asset = get_object_or_denied(Asset, id, user)
        check_permission(info, 'assets.change_asset', obj=asset)
        
        if 'asset_type_id' in kwargs:
            asset.asset_type = get_object_or_denied(AssetType, kwargs.pop('asset_type_id'), user)
        if 'asset_role_id' in kwargs:
            asset.asset_role = get_object_or_denied(AssetRole, kwargs.pop('asset_role_id'), user)
        if 'status_id' in kwargs:
            asset.status = get_object_or_denied(StatusLabel, kwargs.pop('status_id'), user)
        if 'location_id' in kwargs:
            asset.location = get_object_or_denied(Location, kwargs.pop('location_id'), user)
        if 'supplier_id' in kwargs:
            asset.supplier = get_object_or_denied(Supplier, kwargs.pop('supplier_id'), user)
            
        for key, val in kwargs.items():
            setattr(asset, key, val)
            
        try:
            asset.full_clean()
        except ValidationError as e:
            raise Exception(str(e.message_dict if hasattr(e, 'message_dict') else e.messages))
        asset.save()
        return UpdateAsset(asset=asset)

class DeleteAsset(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)

    success = graphene.Boolean()

    def mutate(self, info, id):
        user = check_permission(info, 'assets.delete_asset')
        asset = get_object_or_denied(Asset, id, user)
        check_permission(info, 'assets.delete_asset', obj=asset)
        asset.delete()
        return DeleteAsset(success=True)

class Mutation(graphene.ObjectType):
    create_asset = CreateAsset.Field()
    update_asset = UpdateAsset.Field()
    delete_asset = DeleteAsset.Field()
