"""Organization-owned template context processors."""

from collections import defaultdict

from django.utils.functional import SimpleLazyObject

from core.tenant_scope import accessible_tenant_ids
from organization.models import Membership, Tenant


def _bucket_by_group(tenants):
    """Bucket tenants alphabetically by group, with ungrouped tenants last."""
    group_map = defaultdict(list)
    for tenant in tenants:
        group_map[tenant.group].append(tenant)

    sorted_groups = sorted((group for group in group_map if group is not None), key=lambda group: group.name.lower())
    grouped = [{"group": group, "tenants": group_map[group]} for group in sorted_groups]
    if None in group_map:
        grouped.append({"group": None, "tenants": group_map[None]})
    return grouped


def _all_tenants(user):
    if not user.is_superuser:
        return []
    return Tenant._base_manager.all().order_by("name")


def _grouped_tenants(user):
    if not user.is_superuser:
        return []
    tenants = Tenant._base_manager.all().select_related("group").order_by("group__name", "name")
    return _bucket_by_group(tenants)


def _direct_membership_tenant_ids(user):
    """Return active direct memberships; suspended memberships cannot switch."""
    return set(
        Membership._base_manager.filter(
            user=user,
            is_active=True,
        ).values_list("tenant_id", flat=True)
    )


def _own_tenants(user):
    """Return direct memberships with provider tenants pinned first."""
    if user.is_superuser:
        return []
    direct_ids = _direct_membership_tenant_ids(user)
    if not direct_ids:
        return []
    return list(Tenant._base_manager.filter(pk__in=direct_ids).order_by("-is_provider", "name"))


def _grouped_managed_tenants(user):
    """Return reachable tenants without a direct membership, grouped as before."""
    if user.is_superuser:
        return []
    all_ids = accessible_tenant_ids(user)
    managed_ids = all_ids - _direct_membership_tenant_ids(user)
    if not managed_ids:
        return []
    tenants = Tenant._base_manager.filter(pk__in=managed_ids).select_related("group").order_by("group__name", "name")
    return _bucket_by_group(tenants)


def tenant_switcher_processor(request):
    """Provide lazy, structured tenant lists for the workspace switcher."""
    if not request.user.is_authenticated:
        return {
            "all_tenants_switcher": [],
            "grouped_tenants_switcher": [],
            "own_tenants_switcher": [],
            "grouped_managed_tenants_switcher": [],
        }

    user = request.user
    return {
        "all_tenants_switcher": SimpleLazyObject(lambda: _all_tenants(user)),
        "grouped_tenants_switcher": SimpleLazyObject(lambda: _grouped_tenants(user)),
        "own_tenants_switcher": SimpleLazyObject(lambda: _own_tenants(user)),
        "grouped_managed_tenants_switcher": SimpleLazyObject(lambda: _grouped_managed_tenants(user)),
    }
