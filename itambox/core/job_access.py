"""Canonical visibility policy for administrative Jobs."""

from django.apps import apps
from django.db.models import Q, QuerySet

from core.context import get_current_tenant, get_current_tenant_group
from core.models import Job
from core.tenant_scope import accessible_tenant_ids, get_descendant_tenant_group_ids


def visible_jobs_for_user(user) -> QuerySet:
    """Return the Jobs visible to ``user`` in the current request scope.

    Job has no tenant-scoping manager, so its complete visibility policy lives
    here rather than in a presentation module. For non-superusers, a Job's
    anchor tenant must be in the active scope and every persisted tenant in
    ``data["scope_tenant_ids"]`` must fit inside that same scope. The JSON
    containment predicates intentionally preserve PostgreSQL's array
    containment semantics and the legacy missing-key fallback.
    """
    if user.is_superuser:
        return Job.objects.all()

    tenant = get_current_tenant()
    if tenant is not None:
        allowed_scope = [tenant.pk]
        return Job.objects.filter(tenant=tenant).filter(
            Q(data__scope_tenant_ids__isnull=True) | Q(data__scope_tenant_ids__contained_by=allowed_scope)
        )

    tenant_ids = set(accessible_tenant_ids(user))
    group = get_current_tenant_group()
    if group is not None:
        Tenant = apps.get_model("organization", "Tenant")
        group_tenant_ids = Tenant._base_manager.filter(
            group_id__in=get_descendant_tenant_group_ids(group.pk, live_only=True),
            deleted_at__isnull=True,
        ).values_list("pk", flat=True)
        tenant_ids &= set(group_tenant_ids)

    if not tenant_ids:
        return Job.objects.none()

    allowed_scope = sorted(tenant_ids)
    return Job.objects.filter(tenant_id__in=allowed_scope).filter(
        Q(data__scope_tenant_ids__isnull=True) | Q(data__scope_tenant_ids__contained_by=allowed_scope)
    )
