import graphene
from graphene_django import DjangoObjectType

from core.graphql_utils import check_permission, paginate_queryset

from .models import Software


class SoftwareNode(DjangoObjectType):
    class Meta:
        model = Software
        fields = (
            "id",
            "name",
            "manufacturer",
            "version",
            "category",
            "license_type",
            "website",
            "description",
            "created_at",
            "updated_at",
        )


SOFTWARE_SORTABLE_FIELDS = {
    "name",
    "-name",
    "version",
    "-version",
    "category",
    "-category",
    "created_at",
    "-created_at",
    "updated_at",
    "-updated_at",
}


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
        check_permission(info, "software.view_software")
        qs = Software.objects.select_related("manufacturer").all()
        for key, val in kwargs.items():
            if val is not None:
                qs = qs.filter(**{key: val})
        if sort_by and sort_by in SOFTWARE_SORTABLE_FIELDS:
            qs = qs.order_by(sort_by)
        return paginate_queryset(qs, limit, offset)

    def resolve_software(self, info, id):
        check_permission(info, "software.view_software")
        try:
            return Software.objects.select_related("manufacturer").get(pk=id)
        except Software.DoesNotExist:
            return None
