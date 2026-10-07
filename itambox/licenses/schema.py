import graphene
from graphene_django import DjangoObjectType

from core.graphql_utils import check_permission, paginate_queryset

from .models import License


class LicenseNode(DjangoObjectType):
    class Meta:
        model = License
        fields = (
            "id",
            "name",
            "software",
            "license_type",
            "seats",
            "purchase_date",
            "order_number",
            "expiration_date",
            "supplier",
            "tenant",
            "created_at",
            "updated_at",
        )


LICENSE_SORTABLE_FIELDS = {
    "name",
    "-name",
    "purchase_date",
    "-purchase_date",
    "expiration_date",
    "-expiration_date",
    "seats",
    "-seats",
    "created_at",
    "-created_at",
    "updated_at",
    "-updated_at",
}


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
        check_permission(info, "licenses.view_license")
        active_tenant = getattr(info.context, "active_tenant", None)
        qs = License.objects.select_related("software", "software__manufacturer", "supplier", "tenant").filter(
            tenant=active_tenant
        )
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        if sort_by and sort_by in LICENSE_SORTABLE_FIELDS:
            qs = qs.order_by(sort_by)
        return paginate_queryset(qs, limit, offset)

    def resolve_license(self, info, id):
        check_permission(info, "licenses.view_license")
        active_tenant = getattr(info.context, "active_tenant", None)
        try:
            return (
                License.objects.select_related("software", "software__manufacturer", "supplier", "tenant")
                .filter(tenant=active_tenant)
                .get(pk=id)
            )
        except License.DoesNotExist:
            return None
