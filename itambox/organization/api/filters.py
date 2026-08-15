import django_filters

from organization.models import TenantResourceGrant


class TenantResourceGrantAuditFilterSet(django_filters.FilterSet):
    state = django_filters.ChoiceFilter(
        choices=(("active", "Active"), ("revoked", "Revoked")),
        method="filter_state",
    )
    owner_tenant_id = django_filters.NumberFilter(field_name="tenant_id")
    grantee_tenant_id = django_filters.NumberFilter(field_name="grantee_tenant_id")
    grantee_tenant_group_id = django_filters.NumberFilter(field_name="grantee_tenant_group_id")
    resource_type_id = django_filters.NumberFilter(field_name="resource_type_id")
    resource_id = django_filters.NumberFilter(field_name="resource_id")
    access_level = django_filters.ChoiceFilter(choices=TenantResourceGrant.ACCESS_CHOICES)
    valid_until_before = django_filters.DateTimeFilter(field_name="valid_until", lookup_expr="lt")
    valid_until_after = django_filters.DateTimeFilter(field_name="valid_until", lookup_expr="gt")
    revoked_before = django_filters.DateTimeFilter(field_name="deleted_at", lookup_expr="lt")
    revoked_after = django_filters.DateTimeFilter(field_name="deleted_at", lookup_expr="gt")

    class Meta:
        model = TenantResourceGrant
        fields = (
            "state",
            "owner_tenant_id",
            "grantee_tenant_id",
            "grantee_tenant_group_id",
            "resource_type_id",
            "resource_id",
            "access_level",
            "valid_until_before",
            "valid_until_after",
            "revoked_before",
            "revoked_after",
        )

    def filter_state(self, queryset, name, value):
        if value == "active":
            return queryset.filter(deleted_at__isnull=True)
        if value == "revoked":
            return queryset.filter(deleted_at__isnull=False)
        return queryset
