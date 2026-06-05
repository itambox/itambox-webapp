import graphene
from graphene_django import DjangoObjectType
from .models import Software
from assets.models import Manufacturer
from core.graphql_utils import check_permission, get_object_or_denied, paginate_queryset
from graphql import GraphQLError
from django.core.exceptions import ValidationError

class SoftwareNode(DjangoObjectType):
    class Meta:
        model = Software
        fields = ("id", "name", "manufacturer", "version", "category", "license_type", "website", "description", "created_at", "updated_at")

SOFTWARE_SORTABLE_FIELDS = {"name", "-name", "version", "-version", "category", "-category", "created_at", "-created_at", "updated_at", "-updated_at"}

class Query(graphene.ObjectType):
    software_list = graphene.List(
        SoftwareNode,
        limit=graphene.Int(),
        offset=graphene.Int(),
        sort_by=graphene.String(),
        name=graphene.String(),
        version=graphene.String(),
    )
    software = graphene.Field(SoftwareNode, id=graphene.ID(required=True))

    def resolve_software_list(self, info, limit=None, offset=None, sort_by=None, **kwargs):
        check_permission(info, 'software.view_software')
        qs = Software.objects.select_related('manufacturer').all()
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        if sort_by and sort_by in SOFTWARE_SORTABLE_FIELDS:
            qs = qs.order_by(sort_by)
        return paginate_queryset(qs, limit, offset)

    def resolve_software(self, info, id):
        check_permission(info, 'software.view_software')
        try:
            return Software.objects.select_related('manufacturer').get(pk=id)
        except Software.DoesNotExist:
            return None

class CreateSoftware(graphene.Mutation):
    class Arguments:
        name = graphene.String(required=True)
        manufacturer_id = graphene.ID(required=True)
        version = graphene.String()
        category = graphene.String()
        license_type = graphene.String()
        website = graphene.String()
        description = graphene.String()

    software = graphene.Field(SoftwareNode)

    def mutate(self, info, manufacturer_id, **kwargs):
        user = check_permission(info, 'software.add_software')
        active_tenant = getattr(info.context, 'active_tenant', None)
        manufacturer = get_object_or_denied(Manufacturer, manufacturer_id, user, tenant=active_tenant)
        
        software = Software(manufacturer=manufacturer)
        ALLOWED_FIELDS = {'name', 'version', 'category', 'license_type', 'website', 'description'}
        for key, val in kwargs.items():
            if key in ALLOWED_FIELDS:
                setattr(software, key, val)
                
        try:
            software.full_clean()
        except ValidationError as e:
            raise GraphQLError(
                "Validation failed",
                extensions={"validation_errors": e.message_dict if hasattr(e, 'message_dict') else e.messages}
            )
        software.save()
        return CreateSoftware(software=software)

class UpdateSoftware(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)
        name = graphene.String()
        manufacturer_id = graphene.ID()
        version = graphene.String()
        category = graphene.String()
        license_type = graphene.String()
        website = graphene.String()
        description = graphene.String()

    software = graphene.Field(SoftwareNode)

    def mutate(self, info, id, **kwargs):
        user = check_permission(info, 'software.change_software')
        active_tenant = getattr(info.context, 'active_tenant', None)
        software = get_object_or_denied(Software, id, user, tenant=active_tenant)
        check_permission(info, 'software.change_software', obj=software)
        
        if 'manufacturer_id' in kwargs:
            software.manufacturer = get_object_or_denied(Manufacturer, kwargs.pop('manufacturer_id'), user, tenant=active_tenant)
            
        ALLOWED_FIELDS = {'name', 'version', 'category', 'license_type', 'website', 'description'}
        for key, val in kwargs.items():
            if key in ALLOWED_FIELDS:
                setattr(software, key, val)
            
        try:
            software.full_clean()
        except ValidationError as e:
            raise GraphQLError(
                "Validation failed",
                extensions={"validation_errors": e.message_dict if hasattr(e, 'message_dict') else e.messages}
            )
        software.save()
        return UpdateSoftware(software=software)

class DeleteSoftware(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)

    success = graphene.Boolean()

    def mutate(self, info, id):
        user = check_permission(info, 'software.delete_software')
        active_tenant = getattr(info.context, 'active_tenant', None)
        software = get_object_or_denied(Software, id, user, tenant=active_tenant)
        check_permission(info, 'software.delete_software', obj=software)
        software.delete()
        return DeleteSoftware(success=True)

class Mutation(graphene.ObjectType):
    create_software = CreateSoftware.Field()
    update_software = UpdateSoftware.Field()
    delete_software = DeleteSoftware.Field()
