import graphene
from graphene_django import DjangoObjectType
from .models import License
from software.models import Software
from assets.models import Supplier
from core.graphql_utils import check_permission, get_object_or_denied, paginate_queryset
from graphql import GraphQLError
from django.core.exceptions import ValidationError

class LicenseNode(DjangoObjectType):
    class Meta:
        model = License
        fields = ("id", "name", "software", "license_type", "seats", "purchase_date", "order_number", "expiration_date", "supplier", "tenant", "created_at", "updated_at")

LICENSE_SORTABLE_FIELDS = {"name", "-name", "purchase_date", "-purchase_date", "expiration_date", "-expiration_date", "seats", "-seats", "created_at", "-created_at", "updated_at", "-updated_at"}

class Query(graphene.ObjectType):
    licenses = graphene.List(
        LicenseNode,
        limit=graphene.Int(),
        offset=graphene.Int(),
        sort_by=graphene.String(),
        name=graphene.String(),
    )
    license = graphene.Field(LicenseNode, id=graphene.ID(required=True))

    def resolve_licenses(self, info, limit=None, offset=None, sort_by=None, **kwargs):
        check_permission(info, 'licenses.view_license')
        active_tenant = getattr(info.context, 'active_tenant', None)
        qs = License.objects.filter(tenant=active_tenant)
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        if sort_by and sort_by in LICENSE_SORTABLE_FIELDS:
            qs = qs.order_by(sort_by)
        return paginate_queryset(qs, limit, offset)

    def resolve_license(self, info, id):
        check_permission(info, 'licenses.view_license')
        active_tenant = getattr(info.context, 'active_tenant', None)
        try:
            return License.objects.filter(tenant=active_tenant).get(pk=id)
        except License.DoesNotExist:
            return None

class CreateLicense(graphene.Mutation):
    class Arguments:
        name = graphene.String(required=True)
        software_id = graphene.ID(required=True)
        license_type = graphene.String()
        product_key = graphene.String()
        seats = graphene.Int()
        purchase_date = graphene.Date()
        purchase_cost = graphene.Float()
        order_number = graphene.String()
        expiration_date = graphene.Date()
        notes = graphene.String()
        supplier_id = graphene.ID()

    license = graphene.Field(LicenseNode)

    def mutate(self, info, software_id, **kwargs):
        user = check_permission(info, 'licenses.add_license')
        active_tenant = getattr(info.context, 'active_tenant', None)
        
        software = get_object_or_denied(Software, software_id, user, tenant=active_tenant)
        lic = License(software=software, tenant=active_tenant)
        
        if 'supplier_id' in kwargs:
            lic.supplier = get_object_or_denied(Supplier, kwargs.pop('supplier_id'), user, tenant=active_tenant)
            
        ALLOWED_FIELDS = {'name', 'license_type', 'product_key', 'seats', 'purchase_date', 'purchase_cost', 'order_number', 'expiration_date', 'notes'}
        for key, val in kwargs.items():
            if key in ALLOWED_FIELDS:
                setattr(lic, key, val)
            
        try:
            lic.full_clean()
        except ValidationError as e:
            raise GraphQLError(
                "Validation failed",
                extensions={"validation_errors": e.message_dict if hasattr(e, 'message_dict') else e.messages}
            )
        lic.save()
        return CreateLicense(license=lic)

class UpdateLicense(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)
        name = graphene.String()
        software_id = graphene.ID()
        license_type = graphene.String()
        product_key = graphene.String()
        seats = graphene.Int()
        purchase_date = graphene.Date()
        purchase_cost = graphene.Float()
        order_number = graphene.String()
        expiration_date = graphene.Date()
        notes = graphene.String()
        supplier_id = graphene.ID()

    license = graphene.Field(LicenseNode)

    def mutate(self, info, id, **kwargs):
        user = check_permission(info, 'licenses.change_license')
        active_tenant = getattr(info.context, 'active_tenant', None)
        lic = get_object_or_denied(License, id, user, tenant=active_tenant)
        check_permission(info, 'licenses.change_license', obj=lic)
        
        if 'software_id' in kwargs:
            lic.software = get_object_or_denied(Software, kwargs.pop('software_id'), user, tenant=active_tenant)
        if 'supplier_id' in kwargs:
            lic.supplier = get_object_or_denied(Supplier, kwargs.pop('supplier_id'), user, tenant=active_tenant)
            
        ALLOWED_FIELDS = {'name', 'license_type', 'product_key', 'seats', 'purchase_date', 'purchase_cost', 'order_number', 'expiration_date', 'notes'}
        for key, val in kwargs.items():
            if key in ALLOWED_FIELDS:
                setattr(lic, key, val)
            
        try:
            lic.full_clean()
        except ValidationError as e:
            raise GraphQLError(
                "Validation failed",
                extensions={"validation_errors": e.message_dict if hasattr(e, 'message_dict') else e.messages}
            )
        lic.save()
        return UpdateLicense(license=lic)

class DeleteLicense(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)

    success = graphene.Boolean()

    def mutate(self, info, id):
        user = check_permission(info, 'licenses.delete_license')
        active_tenant = getattr(info.context, 'active_tenant', None)
        lic = get_object_or_denied(License, id, user, tenant=active_tenant)
        check_permission(info, 'licenses.delete_license', obj=lic)
        lic.delete()
        return DeleteLicense(success=True)

class Mutation(graphene.ObjectType):
    create_license = CreateLicense.Field()
    update_license = UpdateLicense.Field()
    delete_license = DeleteLicense.Field()
