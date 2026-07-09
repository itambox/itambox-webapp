"""Domain/service-layer helpers for the organization app.

Currently holds the container-visibility helper shared by the ``Membership``
UI views and any model-agnostic code (e.g. ``ObjectExportView``) that needs
the same restriction applied to a model whose default manager does not
filter by tenant.
"""
from django.db.models import Q

from .models import Tenant, Provider


def visible_to_containers(user, qs, perm):
    """Restrict a queryset of container-scoped rows (rows carrying a
    ``tenant`` and/or ``provider`` FK, e.g. ``Membership``, ``users.Token``)
    to the tenants/providers ``user`` actually holds ``perm`` in.

    Some models intentionally use Django's plain, unscoped default manager
    (``Membership``'s tenant resolution is itself the input to tenant
    scoping, so it can't ride the tenant-scoping manager — see the comment
    in ``core/managers.py``'s ``TenantScopingQuerySet.filter_by_tenant()``).
    That means ``TenantScopingViewMixin.get_queryset()`` — and any other
    generic, model-agnostic view built on ``filter_by_tenant()``, such as
    ``ObjectExportView`` — is a silent no-op for them, so callers must
    restrict the queryset manually with this helper. Superusers are
    unaffected (``PermissionsMixin.has_perm`` short-circuits ``True`` before
    any backend runs), and legitimate multi-tenant/multi-provider staff still
    see every container they hold ``perm`` in.
    """
    if user.is_superuser:
        return qs
    allowed_tenants = [
        t.pk for t in Tenant._base_manager.filter(deleted_at__isnull=True)
        if user.has_perm(perm, obj=t)
    ]
    allowed_providers = [
        p.pk for p in Provider._base_manager.filter(deleted_at__isnull=True)
        if user.has_perm(perm, obj=p)
    ]
    return qs.filter(Q(tenant_id__in=allowed_tenants) | Q(provider_id__in=allowed_providers))


def is_container_scoped_unfiltered(model):
    """True for models like ``Membership``/``users.Token`` that carry both a
    ``tenant`` and a ``provider`` FK but whose default manager does not
    filter by tenant (see ``visible_to_containers``). Model-agnostic code
    that would otherwise rely on ``filter_by_tenant`` (e.g.
    ``ObjectExportView``) uses this to detect when it must apply
    ``visible_to_containers`` instead.
    """
    if hasattr(model.objects, 'filter_by_tenant'):
        return False
    field_names = {f.name for f in model._meta.get_fields()}
    return {'tenant', 'provider'}.issubset(field_names)
