"""Domain/service-layer helpers for the organization app.

Currently holds the tenant-visibility helper shared by the ``Membership``
UI views and any model-agnostic code (e.g. ``ObjectExportView``) that needs
the same restriction applied to a model whose default manager does not
filter by tenant.
"""
from .access import accessible_tenant_ids
from .models import Tenant


# Access-control models whose default manager is deliberately unscoped (their
# tenant resolution is itself an *input* to tenant scoping, so they cannot ride
# the tenant-scoping manager — see TenantScopingQuerySet.filter_by_tenant() in
# core/managers.py). Generic, model-agnostic code (ObjectExportView) must apply
# ``visible_to_containers`` to these instead.
_UNFILTERED_CONTAINER_MODELS = {
    ('organization', 'membership'),
    ('organization', 'roleassignment'),
    ('users', 'token'),
}


def visible_to_containers(user, qs, perm):
    """Restrict a queryset of tenant-anchored rows (``Membership``,
    ``RoleAssignment``, ``users.Token``) to the tenants ``user`` actually
    holds ``perm`` in.

    These models intentionally use an unscoped default manager, so
    ``TenantScopingViewMixin.get_queryset()`` — and any other generic,
    model-agnostic view built on ``filter_by_tenant()``, such as
    ``ObjectExportView`` — is a silent no-op for them; callers must restrict
    the queryset manually with this helper. Candidate tenants come from
    ``accessible_tenant_ids`` (membership + group + managed reach), then each
    is checked with ``user.has_perm(perm, obj=tenant)``. Superusers are
    unaffected (``PermissionsMixin.has_perm`` short-circuits ``True`` before
    any backend runs); multi-tenant staff still see every tenant they hold
    ``perm`` in, including via managed reach.
    """
    if user.is_superuser:
        return qs
    candidate_ids = accessible_tenant_ids(user)
    allowed = [
        t.pk for t in Tenant._base_manager.filter(
            pk__in=candidate_ids, deleted_at__isnull=True,
        )
        if user.has_perm(perm, obj=t)
    ]
    model = qs.model
    field_names = {f.name for f in model._meta.get_fields()}
    if 'tenant' in field_names:
        return qs.filter(tenant_id__in=allowed)
    if 'membership' in field_names:
        # RoleAssignment-shaped rows anchor to a tenant via their membership.
        return qs.filter(membership__tenant_id__in=allowed)
    return qs.none()  # unknown shape — fail closed


def is_container_scoped_unfiltered(model):
    """True for the access-control models (``Membership``, ``RoleAssignment``,
    ``users.Token``) that carry a tenant anchor but whose default manager does
    not filter by tenant (see ``visible_to_containers``). Model-agnostic code
    that would otherwise rely on ``filter_by_tenant`` (e.g. ``ObjectExportView``)
    uses this to detect when it must apply ``visible_to_containers`` instead.
    """
    return (model._meta.app_label, model._meta.model_name) in _UNFILTERED_CONTAINER_MODELS
