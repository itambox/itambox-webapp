import graphene
from graphene_django import DjangoObjectType
from .models import Accessory, Consumable, Kit, Component
from assets.models import Manufacturer, Category, Supplier
from core.graphql_utils import check_permission, get_object_or_denied, generate_slug, paginate_queryset
from graphql import GraphQLError
from django.core.exceptions import ValidationError, PermissionDenied
from django.utils.translation import gettext_lazy as _

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


class ComponentNode(DjangoObjectType):
    min_stock_level = graphene.Int()
    description = graphene.String()

    class Meta:
        model = Component
        fields = ("id", "name", "slug", "manufacturer", "category", "part_number", "allow_overallocate", "tenant", "created_at", "updated_at")

    def resolve_min_stock_level(self, info):
        return self.min_qty

    def resolve_description(self, info):
        return self.notes


ACCESSORY_SORTABLE_FIELDS = {"name", "-name", "slug", "-slug", "part_number", "-part_number", "created_at", "-created_at", "updated_at", "-updated_at"}
CONSUMABLE_SORTABLE_FIELDS = {"name", "-name", "slug", "-slug", "part_number", "-part_number", "created_at", "-created_at", "updated_at", "-updated_at"}
KIT_SORTABLE_FIELDS = {"name", "-name", "created_at", "-created_at", "updated_at", "-updated_at"}
COMPONENT_SORTABLE_FIELDS = {"name", "-name", "slug", "-slug", "part_number", "-part_number", "created_at", "-created_at", "updated_at", "-updated_at"}

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
        check_permission(info, 'inventory.view_accessory')
        active_tenant = getattr(info.context, 'active_tenant', None)
        qs = Accessory.objects.select_related(
            'manufacturer',
            'category',
            'supplier',
            'tenant'
        ).filter(tenant=active_tenant)
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        if sort_by and sort_by in ACCESSORY_SORTABLE_FIELDS:
            qs = qs.order_by(sort_by)
        return paginate_queryset(qs, limit, offset)

    def resolve_accessory(self, info, id):
        check_permission(info, 'inventory.view_accessory')
        active_tenant = getattr(info.context, 'active_tenant', None)
        try:
            return Accessory.objects.select_related(
                'manufacturer',
                'category',
                'supplier',
                'tenant'
            ).filter(tenant=active_tenant).get(pk=id)
        except Accessory.DoesNotExist:
            return None

    def resolve_consumables(self, info, limit=None, offset=None, sort_by=None, **kwargs):
        check_permission(info, 'inventory.view_consumable')
        active_tenant = getattr(info.context, 'active_tenant', None)
        qs = Consumable.objects.select_related(
            'manufacturer',
            'category',
            'tenant'
        ).filter(tenant=active_tenant)
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        if sort_by and sort_by in CONSUMABLE_SORTABLE_FIELDS:
            qs = qs.order_by(sort_by)
        return paginate_queryset(qs, limit, offset)

    def resolve_consumable(self, info, id):
        check_permission(info, 'inventory.view_consumable')
        active_tenant = getattr(info.context, 'active_tenant', None)
        try:
            return Consumable.objects.select_related(
                'manufacturer',
                'category',
                'tenant'
            ).filter(tenant=active_tenant).get(pk=id)
        except Consumable.DoesNotExist:
            return None

    def resolve_kits(self, info, limit=None, offset=None, sort_by=None, **kwargs):
        check_permission(info, 'inventory.view_kit')
        active_tenant = getattr(info.context, 'active_tenant', None)
        qs = Kit.objects.select_related('tenant').filter(tenant=active_tenant)
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        if sort_by and sort_by in KIT_SORTABLE_FIELDS:
            qs = qs.order_by(sort_by)
        return paginate_queryset(qs, limit, offset)

    def resolve_kit(self, info, id):
        check_permission(info, 'inventory.view_kit')
        active_tenant = getattr(info.context, 'active_tenant', None)
        try:
            return Kit.objects.select_related('tenant').filter(tenant=active_tenant).get(pk=id)
        except Kit.DoesNotExist:
            return None

    def resolve_components(self, info, limit=None, offset=None, sort_by=None, **kwargs):
        check_permission(info, 'inventory.view_component')
        active_tenant = getattr(info.context, 'active_tenant', None)
        qs = Component.objects.select_related(
            'manufacturer',
            'category',
            'tenant'
        ).filter(tenant=active_tenant)
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        if sort_by and sort_by in COMPONENT_SORTABLE_FIELDS:
            qs = qs.order_by(sort_by)
        return paginate_queryset(qs, limit, offset)

    def resolve_component(self, info, id):
        check_permission(info, 'inventory.view_component')
        active_tenant = getattr(info.context, 'active_tenant', None)
        try:
            return Component.objects.select_related(
                'manufacturer',
                'category',
                'tenant'
            ).filter(tenant=active_tenant).get(pk=id)
        except Component.DoesNotExist:
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
        
        mfr = get_object_or_denied(Manufacturer, manufacturer_id, user, tenant=active_tenant)
        acc = Accessory(manufacturer=mfr, tenant=active_tenant)

        # A globally-visible (tenant=None) row is visible to every tenant.
        # Without this guard a tenant member in a context where active_tenant is
        # None could mint a global Accessory (REST path is already protected by
        # StrictTenantPermission + perform_create).
        if acc.tenant is None and not user.is_superuser:
            raise PermissionDenied(_("Only superusers can create global accessories."))

        if 'category_id' in kwargs:
            acc.category = get_object_or_denied(Category, kwargs.pop('category_id'), user, tenant=active_tenant)
        if 'supplier_id' in kwargs:
            acc.supplier = get_object_or_denied(Supplier, kwargs.pop('supplier_id'), user, tenant=active_tenant)
            
        ALLOWED_FIELDS = {'name', 'part_number', 'min_qty', 'allow_overallocate', 'notes'}
        for key, val in kwargs.items():
            if key in ALLOWED_FIELDS:
                setattr(acc, key, val)
            
        generate_slug(acc)
        try:
            acc.full_clean()
        except ValidationError as e:
            raise GraphQLError(
                "Validation failed",
                extensions={"validation_errors": e.message_dict if hasattr(e, 'message_dict') else e.messages}
            )
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
        active_tenant = getattr(info.context, 'active_tenant', None)
        acc = get_object_or_denied(Accessory, id, user, tenant=active_tenant)
        # A global (tenant=None) row is reachable by a non-superuser in a tenant-group
        # context: active_tenant is None there, so get_object_or_denied skips its tenant
        # filter and the allow_global_tenant manager returns tenant=None rows. Only a
        # superuser may modify/delete a global catalogue row — mirrors the create guard.
        if acc.tenant is None and not user.is_superuser:
            raise PermissionDenied(_("Only superusers can modify global accessories."))
        check_permission(info, 'inventory.change_accessory', obj=acc)
        
        if 'manufacturer_id' in kwargs:
            acc.manufacturer = get_object_or_denied(Manufacturer, kwargs.pop('manufacturer_id'), user, tenant=active_tenant)
        if 'category_id' in kwargs:
            acc.category = get_object_or_denied(Category, kwargs.pop('category_id'), user, tenant=active_tenant)
        if 'supplier_id' in kwargs:
            acc.supplier = get_object_or_denied(Supplier, kwargs.pop('supplier_id'), user, tenant=active_tenant)
            
        ALLOWED_FIELDS = {'name', 'part_number', 'min_qty', 'allow_overallocate', 'notes'}
        for key, val in kwargs.items():
            if key in ALLOWED_FIELDS:
                setattr(acc, key, val)
            
        try:
            acc.full_clean()
        except ValidationError as e:
            raise GraphQLError(
                "Validation failed",
                extensions={"validation_errors": e.message_dict if hasattr(e, 'message_dict') else e.messages}
            )
        acc.save()
        return UpdateAccessory(accessory=acc)

class DeleteAccessory(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)

    success = graphene.Boolean()

    def mutate(self, info, id):
        user = check_permission(info, 'inventory.delete_accessory')
        active_tenant = getattr(info.context, 'active_tenant', None)
        acc = get_object_or_denied(Accessory, id, user, tenant=active_tenant)
        if acc.tenant is None and not user.is_superuser:
            raise PermissionDenied(_("Only superusers can delete global accessories."))
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
        
        mfr = get_object_or_denied(Manufacturer, manufacturer_id, user, tenant=active_tenant)
        cons = Consumable(manufacturer=mfr, tenant=active_tenant)

        # A globally-visible (tenant=None) row is visible to every tenant.
        # Without this guard a tenant member in a context where active_tenant is
        # None could mint a global Consumable (REST path is already protected by
        # StrictTenantPermission + perform_create).
        if cons.tenant is None and not user.is_superuser:
            raise PermissionDenied(_("Only superusers can create global consumables."))

        if 'category_id' in kwargs:
            cons.category = get_object_or_denied(Category, kwargs.pop('category_id'), user, tenant=active_tenant)
            
        ALLOWED_FIELDS = {'name', 'part_number', 'min_qty', 'allow_overallocate', 'notes'}
        for key, val in kwargs.items():
            if key in ALLOWED_FIELDS:
                setattr(cons, key, val)
            
        generate_slug(cons)
        try:
            cons.full_clean()
        except ValidationError as e:
            raise GraphQLError(
                "Validation failed",
                extensions={"validation_errors": e.message_dict if hasattr(e, 'message_dict') else e.messages}
            )
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
        active_tenant = getattr(info.context, 'active_tenant', None)
        cons = get_object_or_denied(Consumable, id, user, tenant=active_tenant)
        if cons.tenant is None and not user.is_superuser:
            raise PermissionDenied(_("Only superusers can modify global consumables."))
        check_permission(info, 'inventory.change_consumable', obj=cons)
        
        if 'manufacturer_id' in kwargs:
            cons.manufacturer = get_object_or_denied(Manufacturer, kwargs.pop('manufacturer_id'), user, tenant=active_tenant)
        if 'category_id' in kwargs:
            cons.category = get_object_or_denied(Category, kwargs.pop('category_id'), user, tenant=active_tenant)
            
        ALLOWED_FIELDS = {'name', 'part_number', 'min_qty', 'allow_overallocate', 'notes'}
        for key, val in kwargs.items():
            if key in ALLOWED_FIELDS:
                setattr(cons, key, val)
            
        try:
            cons.full_clean()
        except ValidationError as e:
            raise GraphQLError(
                "Validation failed",
                extensions={"validation_errors": e.message_dict if hasattr(e, 'message_dict') else e.messages}
            )
        cons.save()
        return UpdateConsumable(consumable=cons)

class DeleteConsumable(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)

    success = graphene.Boolean()

    def mutate(self, info, id):
        user = check_permission(info, 'inventory.delete_consumable')
        active_tenant = getattr(info.context, 'active_tenant', None)
        cons = get_object_or_denied(Consumable, id, user, tenant=active_tenant)
        if cons.tenant is None and not user.is_superuser:
            raise PermissionDenied(_("Only superusers can delete global consumables."))
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
        
        kt = Kit(tenant=active_tenant)

        # Kit.allow_global_tenant is True — a tenant=None Kit is visible to every
        # tenant. Without this guard a tenant member in a context where active_tenant
        # is None could mint a global Kit (REST path is already protected by
        # StrictTenantPermission + perform_create).
        if kt.tenant is None and not user.is_superuser:
            raise PermissionDenied(_("Only superusers can create global kits."))

        ALLOWED_FIELDS = {'name', 'description'}
        for key, val in kwargs.items():
            if key in ALLOWED_FIELDS:
                setattr(kt, key, val)

        try:
            kt.full_clean()
        except ValidationError as e:
            raise GraphQLError(
                "Validation failed",
                extensions={"validation_errors": e.message_dict if hasattr(e, 'message_dict') else e.messages}
            )
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
        active_tenant = getattr(info.context, 'active_tenant', None)
        kt = get_object_or_denied(Kit, id, user, tenant=active_tenant)
        if kt.tenant is None and not user.is_superuser:
            raise PermissionDenied(_("Only superusers can modify global kits."))
        check_permission(info, 'inventory.change_kit', obj=kt)
        
        ALLOWED_FIELDS = {'name', 'description'}
        for key, val in kwargs.items():
            if key in ALLOWED_FIELDS:
                setattr(kt, key, val)
            
        try:
            kt.full_clean()
        except ValidationError as e:
            raise GraphQLError(
                "Validation failed",
                extensions={"validation_errors": e.message_dict if hasattr(e, 'message_dict') else e.messages}
            )
        kt.save()
        return UpdateKit(kit=kt)

class DeleteKit(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)

    success = graphene.Boolean()

    def mutate(self, info, id):
        user = check_permission(info, 'inventory.delete_kit')
        active_tenant = getattr(info.context, 'active_tenant', None)
        kt = get_object_or_denied(Kit, id, user, tenant=active_tenant)
        if kt.tenant is None and not user.is_superuser:
            raise PermissionDenied(_("Only superusers can delete global kits."))
        check_permission(info, 'inventory.delete_kit', obj=kt)
        kt.delete()
        return DeleteKit(success=True)


class CreateComponent(graphene.Mutation):
    class Arguments:
        name = graphene.String(required=True)
        manufacturer_id = graphene.ID(required=True)
        category_id = graphene.ID(required=True)
        part_number = graphene.String()
        min_stock_level = graphene.Int()
        description = graphene.String()

    component = graphene.Field(ComponentNode)

    def mutate(self, info, manufacturer_id, category_id, **kwargs):
        user = check_permission(info, 'inventory.add_component')
        active_tenant = getattr(info.context, 'active_tenant', None)
        
        mfr = get_object_or_denied(Manufacturer, manufacturer_id, user, tenant=active_tenant)
        cat = get_object_or_denied(Category, category_id, user, tenant=active_tenant)

        comp = Component(manufacturer=mfr, category=cat, tenant=active_tenant)

        # A globally-visible (tenant=None) row is visible to every tenant.
        # Without this guard a tenant member in a context where active_tenant is
        # None could mint a global Component (REST path is already protected by
        # StrictTenantPermission + perform_create).
        if comp.tenant is None and not user.is_superuser:
            raise PermissionDenied(_("Only superusers can create global components."))

        if 'min_stock_level' in kwargs:
            kwargs['min_qty'] = kwargs.pop('min_stock_level')
        if 'description' in kwargs:
            kwargs['notes'] = kwargs.pop('description')
            
        ALLOWED_FIELDS = {'name', 'part_number', 'min_qty', 'notes'}
        for key, val in kwargs.items():
            if key in ALLOWED_FIELDS:
                setattr(comp, key, val)
            
        generate_slug(comp)
        try:
            comp.full_clean()
        except ValidationError as e:
            raise GraphQLError(
                "Validation failed",
                extensions={"validation_errors": e.message_dict if hasattr(e, 'message_dict') else e.messages}
            )
        comp.save()
        return CreateComponent(component=comp)


class UpdateComponent(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)
        name = graphene.String()
        manufacturer_id = graphene.ID()
        category_id = graphene.ID()
        part_number = graphene.String()
        min_stock_level = graphene.Int()
        description = graphene.String()

    component = graphene.Field(ComponentNode)

    def mutate(self, info, id, **kwargs):
        user = check_permission(info, 'inventory.change_component')
        active_tenant = getattr(info.context, 'active_tenant', None)
        comp = get_object_or_denied(Component, id, user, tenant=active_tenant)
        if comp.tenant is None and not user.is_superuser:
            raise PermissionDenied(_("Only superusers can modify global components."))
        check_permission(info, 'inventory.change_component', obj=comp)
        
        if 'manufacturer_id' in kwargs:
            comp.manufacturer = get_object_or_denied(Manufacturer, kwargs.pop('manufacturer_id'), user, tenant=active_tenant)
        if 'category_id' in kwargs:
            comp.category = get_object_or_denied(Category, kwargs.pop('category_id'), user, tenant=active_tenant)
            
        if 'min_stock_level' in kwargs:
            kwargs['min_qty'] = kwargs.pop('min_stock_level')
        if 'description' in kwargs:
            kwargs['notes'] = kwargs.pop('description')
            
        ALLOWED_FIELDS = {'name', 'part_number', 'min_qty', 'notes'}
        for key, val in kwargs.items():
            if key in ALLOWED_FIELDS:
                setattr(comp, key, val)
            
        try:
            comp.full_clean()
        except ValidationError as e:
            raise GraphQLError(
                "Validation failed",
                extensions={"validation_errors": e.message_dict if hasattr(e, 'message_dict') else e.messages}
            )
        comp.save()
        return UpdateComponent(component=comp)


class DeleteComponent(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)

    success = graphene.Boolean()

    def mutate(self, info, id):
        user = check_permission(info, 'inventory.delete_component')
        active_tenant = getattr(info.context, 'active_tenant', None)
        comp = get_object_or_denied(Component, id, user, tenant=active_tenant)
        if comp.tenant is None and not user.is_superuser:
            raise PermissionDenied(_("Only superusers can delete global components."))
        check_permission(info, 'inventory.delete_component', obj=comp)
        comp.delete()
        return DeleteComponent(success=True)


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

    create_component = CreateComponent.Field()
    update_component = UpdateComponent.Field()
    delete_component = DeleteComponent.Field()
