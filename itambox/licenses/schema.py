import graphene
from graphene_django import DjangoObjectType
from .models import License
from software.models import Software
from assets.models import Supplier
from core.graphql_utils import check_permission, get_object_or_denied
from django.core.exceptions import ValidationError

MAX_PAGINATION_LIMIT = 200

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
        qs = License.objects.all()
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        if sort_by and sort_by in LICENSE_SORTABLE_FIELDS:
            qs = qs.order_by(sort_by)
        if offset is not None:
            qs = qs[offset:]
        limit = min(limit, MAX_PAGINATION_LIMIT) if limit is not None else MAX_PAGINATION_LIMIT
        qs = qs[:limit]
        return qs

    def resolve_license(self, info, id):
        check_permission(info, 'licenses.view_license')
        try:
            return License.objects.get(pk=id)
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
        
        software = get_object_or_denied(Software, software_id, user)
        lic = License(software=software, tenant=active_tenant)
        
        if 'supplier_id' in kwargs:
            lic.supplier = get_object_or_denied(Supplier, kwargs.pop('supplier_id'), user)
            
        for key, val in kwargs.items():
            setattr(lic, key, val)
            
        try:
            lic.full_clean()
        except ValidationError as e:
            raise Exception(str(e.message_dict if hasattr(e, 'message_dict') else e.messages))
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
        lic = get_object_or_denied(License, id, user)
        check_permission(info, 'licenses.change_license', obj=lic)
        
        if 'software_id' in kwargs:
            lic.software = get_object_or_denied(Software, kwargs.pop('software_id'), user)
        if 'supplier_id' in kwargs:
            lic.supplier = get_object_or_denied(Supplier, kwargs.pop('supplier_id'), user)
            
        for key, val in kwargs.items():
            setattr(lic, key, val)
            
        try:
            lic.full_clean()
        except ValidationError as e:
            raise Exception(str(e.message_dict if hasattr(e, 'message_dict') else e.messages))
        lic.save()
        return UpdateLicense(license=lic)

class DeleteLicense(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=True)

    success = graphene.Boolean()

    def mutate(self, info, id):
        user = check_permission(info, 'licenses.delete_license')
        lic = get_object_or_denied(License, id, user)
        check_permission(info, 'licenses.delete_license', obj=lic)
        lic.delete()
        return DeleteLicense(success=True)

class Mutation(graphene.ObjectType):
    create_license = CreateLicense.Field()
    update_license = UpdateLicense.Field()
    delete_license = DeleteLicense.Field()
