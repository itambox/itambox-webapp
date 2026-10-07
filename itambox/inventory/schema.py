import graphene
from graphene_django import DjangoObjectType

from core.graphql_utils import check_permission, paginate_queryset

from .models import Accessory, Component, Consumable, Kit


class AccessoryNode(DjangoObjectType):
    class Meta:
        model = Accessory
        fields = (
            "id",
            "name",
            "slug",
            "manufacturer",
            "category",
            "supplier",
            "part_number",
            "min_qty",
            "allow_overallocate",
            "tenant",
            "created_at",
            "updated_at",
        )


class ConsumableNode(DjangoObjectType):
    class Meta:
        model = Consumable
        fields = (
            "id",
            "name",
            "slug",
            "manufacturer",
            "category",
            "part_number",
            "min_qty",
            "allow_overallocate",
            "tenant",
            "created_at",
            "updated_at",
        )


class KitNode(DjangoObjectType):
    class Meta:
        model = Kit
        fields = ("id", "name", "description", "tenant", "created_at", "updated_at")


class ComponentNode(DjangoObjectType):
    min_stock_level = graphene.Int()
    description = graphene.String()

    class Meta:
        model = Component
        fields = (
            "id",
            "name",
            "slug",
            "manufacturer",
            "category",
            "part_number",
            "allow_overallocate",
            "tenant",
            "created_at",
            "updated_at",
        )

    def resolve_min_stock_level(self, info):
        return self.min_qty

    def resolve_description(self, info):
        return self.notes


ACCESSORY_SORTABLE_FIELDS = {
    "name",
    "-name",
    "slug",
    "-slug",
    "part_number",
    "-part_number",
    "created_at",
    "-created_at",
    "updated_at",
    "-updated_at",
}
CONSUMABLE_SORTABLE_FIELDS = {
    "name",
    "-name",
    "slug",
    "-slug",
    "part_number",
    "-part_number",
    "created_at",
    "-created_at",
    "updated_at",
    "-updated_at",
}
KIT_SORTABLE_FIELDS = {"name", "-name", "created_at", "-created_at", "updated_at", "-updated_at"}
COMPONENT_SORTABLE_FIELDS = {
    "name",
    "-name",
    "slug",
    "-slug",
    "part_number",
    "-part_number",
    "created_at",
    "-created_at",
    "updated_at",
    "-updated_at",
}


class Query(graphene.ObjectType):
    accessories = graphene.List(
        AccessoryNode,
        limit=graphene.Int(),
        offset=graphene.Int(),
        sort_by=graphene.String(),
        name=graphene.String(),
    )
    accessory = graphene.Field(AccessoryNode, id=graphene.ID(required=True))

    components = graphene.List(
        ComponentNode,
        limit=graphene.Int(),
        offset=graphene.Int(),
        sort_by=graphene.String(),
        name=graphene.String(),
    )
    component = graphene.Field(ComponentNode, id=graphene.ID(required=True))

    consumables = graphene.List(
        ConsumableNode,
        limit=graphene.Int(),
        offset=graphene.Int(),
        sort_by=graphene.String(),
        name=graphene.String(),
    )
    consumable = graphene.Field(ConsumableNode, id=graphene.ID(required=True))

    kits = graphene.List(
        KitNode,
        limit=graphene.Int(),
        offset=graphene.Int(),
        sort_by=graphene.String(),
        name=graphene.String(),
    )
    kit = graphene.Field(KitNode, id=graphene.ID(required=True))

    def resolve_accessories(self, info, limit=None, offset=None, sort_by=None, **kwargs):
        check_permission(info, "inventory.view_accessory")
        active_tenant = getattr(info.context, "active_tenant", None)
        qs = Accessory.objects.select_related("manufacturer", "category", "supplier", "tenant").filter(
            tenant=active_tenant
        )
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        if sort_by and sort_by in ACCESSORY_SORTABLE_FIELDS:
            qs = qs.order_by(sort_by)
        return paginate_queryset(qs, limit, offset)

    def resolve_accessory(self, info, id):
        check_permission(info, "inventory.view_accessory")
        active_tenant = getattr(info.context, "active_tenant", None)
        try:
            return (
                Accessory.objects.select_related("manufacturer", "category", "supplier", "tenant")
                .filter(tenant=active_tenant)
                .get(pk=id)
            )
        except Accessory.DoesNotExist:
            return None

    def resolve_consumables(self, info, limit=None, offset=None, sort_by=None, **kwargs):
        check_permission(info, "inventory.view_consumable")
        active_tenant = getattr(info.context, "active_tenant", None)
        qs = Consumable.objects.select_related("manufacturer", "category", "tenant").filter(tenant=active_tenant)
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        if sort_by and sort_by in CONSUMABLE_SORTABLE_FIELDS:
            qs = qs.order_by(sort_by)
        return paginate_queryset(qs, limit, offset)

    def resolve_consumable(self, info, id):
        check_permission(info, "inventory.view_consumable")
        active_tenant = getattr(info.context, "active_tenant", None)
        try:
            return (
                Consumable.objects.select_related("manufacturer", "category", "tenant")
                .filter(tenant=active_tenant)
                .get(pk=id)
            )
        except Consumable.DoesNotExist:
            return None

    def resolve_kits(self, info, limit=None, offset=None, sort_by=None, **kwargs):
        check_permission(info, "inventory.view_kit")
        active_tenant = getattr(info.context, "active_tenant", None)
        qs = Kit.objects.select_related("tenant").filter(tenant=active_tenant)
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        if sort_by and sort_by in KIT_SORTABLE_FIELDS:
            qs = qs.order_by(sort_by)
        return paginate_queryset(qs, limit, offset)

    def resolve_kit(self, info, id):
        check_permission(info, "inventory.view_kit")
        active_tenant = getattr(info.context, "active_tenant", None)
        try:
            return Kit.objects.select_related("tenant").filter(tenant=active_tenant).get(pk=id)
        except Kit.DoesNotExist:
            return None

    def resolve_components(self, info, limit=None, offset=None, sort_by=None, **kwargs):
        check_permission(info, "inventory.view_component")
        active_tenant = getattr(info.context, "active_tenant", None)
        qs = Component.objects.select_related("manufacturer", "category", "tenant").filter(tenant=active_tenant)
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        if sort_by and sort_by in COMPONENT_SORTABLE_FIELDS:
            qs = qs.order_by(sort_by)
        return paginate_queryset(qs, limit, offset)

    def resolve_component(self, info, id):
        check_permission(info, "inventory.view_component")
        active_tenant = getattr(info.context, "active_tenant", None)
        try:
            return (
                Component.objects.select_related("manufacturer", "category", "tenant")
                .filter(tenant=active_tenant)
                .get(pk=id)
            )
        except Component.DoesNotExist:
            return None
