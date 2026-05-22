import graphene
from graphene_django import DjangoObjectType
from .models import Accessory, Consumable, Kit
from assets.models import Manufacturer, Category, Supplier
from core.graphql_utils import check_permission, get_object_or_denied, generate_slug
from django.core.exceptions import ValidationError

MAX_PAGINATION_LIMIT = 200

class AccessoryNode(DjangoObjectType):
    class Meta:
        model = Accessory
        fields = ("id", "name", "slug", "manufacturer", "category", "supplier", "part_number", "min_qty", "allow_overallocate", "tenant", "created_at", "updated_at")

class ConsumableNode(DjangoObjectType):
    class Meta:
        model = Consumable
        fields = ("id", "name", "slug", "manufacturer", "category", "part_number", "min_qty", "allow_overallocate", "tenant", "created_at", "updated_at")

class KitNode(DjangoObjectType):
    class Meta:
        model = Kit
        fields = ("id", "name", "description", "tenant", "created_at", "updated_at")

ACCESSORY_SORTABLE_FIELDS = {"name", "-name", "slug", "-slug", "part_number", "-part_number", "created_at", "-created_at", "updated_at", "-updated_at"}
CONSUMABLE_SORTABLE_FIELDS = {"name", "-name", "slug", "-slug", "part_number", "-part_number", "created_at", "-created_at", "updated_at", "-updated_at"}
KIT_SORTABLE_FIELDS = {"name", "-name", "created_at", "-created_at", "updated_at", "-updated_at"}

class Query(graphene.ObjectType):
    accessories = graphene.List(
        AccessoryNode,
        limit=graphene.Int(),
        offset=graphene.Int(),
        sort_by=graphene.String(),
        name=graphene.String(),
    )
    accessory = graphene.Field(AccessoryNode, id=graphene.ID(required=True))

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
        check_permission(info, 'inventory.view_accessory')
        qs = Accessory.objects.all()
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        if sort_by and sort_by in ACCESSORY_SORTABLE_FIELDS:
            qs = qs.order_by(sort_by)
        if offset is not None:
            qs = qs[offset:]
        limit = min(limit, MAX_PAGINATION_LIMIT) if limit is not None else MAX_PAGINATION_LIMIT
        qs = qs[:limit]
        return qs

    def resolve_accessory(self, info, id):
        check_permission(info, 'inventory.view_accessory')
        try:
            return Accessory.objects.get(pk=id)
        except Accessory.DoesNotExist:
            return None

    def resolve_consumables(self, info, limit=None, offset=None, sort_by=None, **kwargs):
        check_permission(info, 'inventory.view_consumable')
        qs = Consumable.objects.all()
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        if sort_by and sort_by in CONSUMABLE_SORTABLE_FIELDS:
            qs = qs.order_by(sort_by)
        if offset is not None:
            qs = qs[offset:]
        limit = min(limit, MAX_PAGINATION_LIMIT) if limit is not None else MAX_PAGINATION_LIMIT
        qs = qs[:limit]
        return qs

    def resolve_consumable(self, info, id):
        check_permission(info, 'inventory.view_consumable')
        try:
            return Consumable.objects.get(pk=id)
        except Consumable.DoesNotExist:
            return None

    def resolve_kits(self, info, limit=None, offset=None, sort_by=None, **kwargs):
        check_permission(info, 'inventory.view_kit')
        qs = Kit.objects.all()
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        if sort_by and sort_by in KIT_SORTABLE_FIELDS:
            qs = qs.order_by(sort_by)
        if offset is not None:
            qs = qs[offset:]
        limit = min(limit, MAX_PAGINATION_LIMIT) if limit is not None else MAX_PAGINATION_LIMIT
        qs = qs[:limit]
        return qs

    def resolve_kit(self, info, id):
        check_permission(info, 'inventory.view_kit')
        try:
            return Kit.objects.get(pk=id)
        except Kit.DoesNotExist:
            return None

class CreateAccessory(graphene.Mutation):
    class Arguments:
        name = graphene.String(required=True)
        manufacturer_id = graphene.ID(required=True)
        category_id = graphene.ID()
        supplier_id = graphene.ID()
        part_number = graphene.String()
        min_qty = graphene.Int()
        allow_overallocate = graphene.Boolean()
        notes = graphene.String()

    accessory = graphene.Field(AccessoryNode)

    def mutate(self, info, manufacturer_id, **kwargs):
        user = check_permission(info, 'inventory.add_accessory')
        active_tenant = getattr(info.context, 'active_tenant', None)
        
        mfr = get_object_or_denied(Manufacturer, manufacturer_id, user)
        acc = Accessory(manufacturer=mfr, tenant=active_tenant)
        
        if 'category_id' in kwargs:
            acc.category = get_object_or_denied(Category, kwargs.pop('category_id'), user)
        if 'supplier_id' in kwargs:
            acc.supplier = get_object_or_denied(Supplier, kwargs.pop('supplier_id'), user)
            
        for key, val in kwargs.items():
            setattr(acc, key, val)
            
        generate_slug(acc)
        try:
            acc.full_clean()
        except ValidationError as e:
            raise Exception(str(e.message_dict if hasattr(e, 'message_dict') else e.messages))
        acc.save()
        return CreateAccessory(accessory=acc)

class UpdateAccessory(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)
        name = graphene.String()
        manufacturer_id = graphene.ID()
        category_id = graphene.ID()
        supplier_id = graphene.ID()
        part_number = graphene.String()
        min_qty = graphene.Int()
        allow_overallocate = graphene.Boolean()
        notes = graphene.String()

    accessory = graphene.Field(AccessoryNode)

    def mutate(self, info, id, **kwargs):
        user = check_permission(info, 'inventory.change_accessory')
        acc = get_object_or_denied(Accessory, id, user)
        check_permission(info, 'inventory.change_accessory', obj=acc)
        
        if 'manufacturer_id' in kwargs:
            acc.manufacturer = get_object_or_denied(Manufacturer, kwargs.pop('manufacturer_id'), user)
        if 'category_id' in kwargs:
            acc.category = get_object_or_denied(Category, kwargs.pop('category_id'), user)
        if 'supplier_id' in kwargs:
            acc.supplier = get_object_or_denied(Supplier, kwargs.pop('supplier_id'), user)
            
        for key, val in kwargs.items():
            setattr(acc, key, val)
            
        try:
            acc.full_clean()
        except ValidationError as e:
            raise Exception(str(e.message_dict if hasattr(e, 'message_dict') else e.messages))
        acc.save()
        return UpdateAccessory(accessory=acc)

class DeleteAccessory(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)

    success = graphene.Boolean()

    def mutate(self, info, id):
        user = check_permission(info, 'inventory.delete_accessory')
        acc = get_object_or_denied(Accessory, id, user)
        check_permission(info, 'inventory.delete_accessory', obj=acc)
        acc.delete()
        return DeleteAccessory(success=True)

class CreateConsumable(graphene.Mutation):
    class Arguments:
        name = graphene.String(required=True)
        manufacturer_id = graphene.ID(required=True)
        category_id = graphene.ID()
        part_number = graphene.String()
        min_qty = graphene.Int()
        allow_overallocate = graphene.Boolean()
        notes = graphene.String()

    consumable = graphene.Field(ConsumableNode)

    def mutate(self, info, manufacturer_id, **kwargs):
        user = check_permission(info, 'inventory.add_consumable')
        active_tenant = getattr(info.context, 'active_tenant', None)
        
        mfr = get_object_or_denied(Manufacturer, manufacturer_id, user)
        cons = Consumable(manufacturer=mfr, tenant=active_tenant)
        
        if 'category_id' in kwargs:
            cons.category = get_object_or_denied(Category, kwargs.pop('category_id'), user)
            
        for key, val in kwargs.items():
            setattr(cons, key, val)
            
        generate_slug(cons)
        try:
            cons.full_clean()
        except ValidationError as e:
            raise Exception(str(e.message_dict if hasattr(e, 'message_dict') else e.messages))
        cons.save()
        return CreateConsumable(consumable=cons)

class UpdateConsumable(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)
        name = graphene.String()
        manufacturer_id = graphene.ID()
        category_id = graphene.ID()
        part_number = graphene.String()
        min_qty = graphene.Int()
        allow_overallocate = graphene.Boolean()
        notes = graphene.String()

    consumable = graphene.Field(ConsumableNode)

    def mutate(self, info, id, **kwargs):
        user = check_permission(info, 'inventory.change_consumable')
        cons = get_object_or_denied(Consumable, id, user)
        check_permission(info, 'inventory.change_consumable', obj=cons)
        
        if 'manufacturer_id' in kwargs:
            cons.manufacturer = get_object_or_denied(Manufacturer, kwargs.pop('manufacturer_id'), user)
        if 'category_id' in kwargs:
            cons.category = get_object_or_denied(Category, kwargs.pop('category_id'), user)
            
        for key, val in kwargs.items():
            setattr(cons, key, val)
            
        try:
            cons.full_clean()
        except ValidationError as e:
            raise Exception(str(e.message_dict if hasattr(e, 'message_dict') else e.messages))
        cons.save()
        return UpdateConsumable(consumable=cons)

class DeleteConsumable(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)

    success = graphene.Boolean()

    def mutate(self, info, id):
        user = check_permission(info, 'inventory.delete_consumable')
        cons = get_object_or_denied(Consumable, id, user)
        check_permission(info, 'inventory.delete_consumable', obj=cons)
        cons.delete()
        return DeleteConsumable(success=True)

class CreateKit(graphene.Mutation):
    class Arguments:
        name = graphene.String(required=True)
        description = graphene.String()

    kit = graphene.Field(KitNode)

    def mutate(self, info, **kwargs):
        user = check_permission(info, 'inventory.add_kit')
        active_tenant = getattr(info.context, 'active_tenant', None)
        
        kt = Kit(tenant=active_tenant, **kwargs)
        try:
            kt.full_clean()
        except ValidationError as e:
            raise Exception(str(e.message_dict if hasattr(e, 'message_dict') else e.messages))
        kt.save()
        return CreateKit(kit=kt)

class UpdateKit(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)
        name = graphene.String()
        description = graphene.String()

    kit = graphene.Field(KitNode)

    def mutate(self, info, id, **kwargs):
        user = check_permission(info, 'inventory.change_kit')
        kt = get_object_or_denied(Kit, id, user)
        check_permission(info, 'inventory.change_kit', obj=kt)
        
        for key, val in kwargs.items():
            setattr(kt, key, val)
            
        try:
            kt.full_clean()
        except ValidationError as e:
            raise Exception(str(e.message_dict if hasattr(e, 'message_dict') else e.messages))
        kt.save()
        return UpdateKit(kit=kt)

class DeleteKit(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)

    success = graphene.Boolean()

    def mutate(self, info, id):
        user = check_permission(info, 'inventory.delete_kit')
        kt = get_object_or_denied(Kit, id, user)
        check_permission(info, 'inventory.delete_kit', obj=kt)
        kt.delete()
        return DeleteKit(success=True)

class Mutation(graphene.ObjectType):
    create_accessory = CreateAccessory.Field()
    update_accessory = UpdateAccessory.Field()
    delete_accessory = DeleteAccessory.Field()
    
    create_consumable = CreateConsumable.Field()
    update_consumable = UpdateConsumable.Field()
    delete_consumable = DeleteConsumable.Field()
    
    create_kit = CreateKit.Field()
    update_kit = UpdateKit.Field()
    delete_kit = DeleteKit.Field()
