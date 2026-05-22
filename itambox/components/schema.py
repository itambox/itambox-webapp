import graphene
from graphene_django import DjangoObjectType
from .models import Component
from assets.models import Manufacturer, Category
from core.graphql_utils import check_permission, get_object_or_denied, generate_slug
from django.core.exceptions import ValidationError

MAX_PAGINATION_LIMIT = 200

class ComponentNode(DjangoObjectType):
    class Meta:
        model = Component
        fields = ("id", "name", "slug", "manufacturer", "category", "part_number", "min_stock_level", "description", "tenant", "created_at", "updated_at")

COMPONENT_SORTABLE_FIELDS = {"name", "-name", "slug", "-slug", "part_number", "-part_number", "created_at", "-created_at", "updated_at", "-updated_at"}

class Query(graphene.ObjectType):
    components = graphene.List(
        ComponentNode,
        limit=graphene.Int(),
        offset=graphene.Int(),
        sort_by=graphene.String(),
        name=graphene.String(),
    )
    component = graphene.Field(ComponentNode, id=graphene.ID(required=True))

    def resolve_components(self, info, limit=None, offset=None, sort_by=None, **kwargs):
        check_permission(info, 'components.view_component')
        qs = Component.objects.all()
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        if sort_by and sort_by in COMPONENT_SORTABLE_FIELDS:
            qs = qs.order_by(sort_by)
        if offset is not None:
            qs = qs[offset:]
        limit = min(limit, MAX_PAGINATION_LIMIT) if limit is not None else MAX_PAGINATION_LIMIT
        qs = qs[:limit]
        return qs

    def resolve_component(self, info, id):
        check_permission(info, 'components.view_component')
        try:
            return Component.objects.get(pk=id)
        except Component.DoesNotExist:
            return None

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
        user = check_permission(info, 'components.add_component')
        active_tenant = getattr(info.context, 'active_tenant', None)
        
        mfr = get_object_or_denied(Manufacturer, manufacturer_id, user)
        cat = get_object_or_denied(Category, category_id, user)
        
        comp = Component(manufacturer=mfr, category=cat, tenant=active_tenant)
        for key, val in kwargs.items():
            setattr(comp, key, val)
            
        generate_slug(comp)
        try:
            comp.full_clean()
        except ValidationError as e:
            raise Exception(str(e.message_dict if hasattr(e, 'message_dict') else e.messages))
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
        user = check_permission(info, 'components.change_component')
        comp = get_object_or_denied(Component, id, user)
        check_permission(info, 'components.change_component', obj=comp)
        
        if 'manufacturer_id' in kwargs:
            comp.manufacturer = get_object_or_denied(Manufacturer, kwargs.pop('manufacturer_id'), user)
        if 'category_id' in kwargs:
            comp.category = get_object_or_denied(Category, kwargs.pop('category_id'), user)
            
        for key, val in kwargs.items():
            setattr(comp, key, val)
            
        try:
            comp.full_clean()
        except ValidationError as e:
            raise Exception(str(e.message_dict if hasattr(e, 'message_dict') else e.messages))
        comp.save()
        return UpdateComponent(component=comp)

class DeleteComponent(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)

    success = graphene.Boolean()

    def mutate(self, info, id):
        user = check_permission(info, 'components.delete_component')
        comp = get_object_or_denied(Component, id, user)
        check_permission(info, 'components.delete_component', obj=comp)
        comp.delete()
        return DeleteComponent(success=True)

class Mutation(graphene.ObjectType):
    create_component = CreateComponent.Field()
    update_component = UpdateComponent.Field()
    delete_component = DeleteComponent.Field()
