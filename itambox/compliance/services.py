from organization.models import Tenant


def scope_custody_receipts(queryset, *, user, permission=None):
    """Limit receipts to asset tenants in the request's authorized tenant scope.

    CustodyReceipt deliberately has no tenant field and keeps an unscoped default
    manager for its public bearer-token route. Every internal caller must therefore
    pass through this helper, which scopes on ``asset__tenant`` and can additionally
    require a permission in each candidate tenant.
    """
    if user is None or not user.is_authenticated:
        return queryset.none()

    candidate_tenants = Tenant.objects.all()
    if permission and not user.is_superuser:
        tenant_ids = [tenant.pk for tenant in candidate_tenants if user.has_perm(permission, obj=tenant)]
    else:
        tenant_ids = candidate_tenants.values_list("pk", flat=True)

    return queryset.filter(asset__tenant_id__in=tenant_ids)
