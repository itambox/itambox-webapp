"""Assembly of one report context from its domain provider.

This module knows nothing about any individual report: it resolves the tenant
scope, asks the registry for the provider that owns the identifier, hands it an
immutable request, and assembles the common context around the result it gets
back.  Every decision about *what* a report contains belongs to the provider in
the owning domain application.
"""

import logging
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.utils import timezone

from core.context import (
    get_current_all_accessible,
    get_current_scope_conflict,
    get_current_tenant,
    get_current_tenant_group,
    has_valid_system_authorization,
)
from core.reports.contracts import ReportDefinition, ReportPermissionDenied, ReportRequest, ReportRow, ReportSummary
from core.tenant_scope import (
    accessible_tenant_ids,
    build_accessible_tenant_permissions_map,
    get_descendant_tenant_group_ids,
    tenant_model,
)
from itambox.middleware import get_current_user

from .columns import headers_for
from .registry import get_report_provider
from .rendering import report_disclosure_text
from .rows import DEFAULT_GROUP, GROUP_FIELD

_CROSS_TENANT_PERMISSION_MESSAGE = (
    "Cross-tenant report aggregation requires the 'reports.view_cross_tenant_reports' permission."
)
_PINNED_TENANT_UNREACHABLE_MESSAGE = "This report is pinned to a tenant the acting user cannot access."
REPORT_COMPILATION_OPERATION = "reports.compile"
_REPORT_PERMISSION_DENIED_MESSAGE = "The acting principal may not compile this report for the effective tenant scope."
logger = logging.getLogger(__name__)


def _tenant_scope_reach_is_valid(user: object, tenant: object) -> bool:
    """Whether ``user`` holds the cross-tenant reporting permission for ``tenant``.

    Mirrors the scheduled-report approval check (``extras.tasks.reports``):
    per-tenant role resolution needs the tenant bound, so the permission is
    evaluated inside a task context scoped to that tenant instead of against
    ambient state. Any resolution failure counts as "no permission".
    """
    # inline import: app-registry: the task context module is only needed on
    # the pinned-constellation path.
    from core.tasks.context import TaskContext

    tenant_id = getattr(tenant, "pk", None)
    user_id = getattr(user, "pk", None)
    if tenant_id is None or user_id is None:
        return False
    try:
        with TaskContext(tenant_id=tenant_id, user_id=user_id, operation="reports.scope"):
            bound_user = get_current_user()
            return bound_user is not None and bound_user.has_perm(
                "reports.view_cross_tenant_reports",
                obj=tenant,
            )
    except (ObjectDoesNotExist, PermissionDenied):
        return False


def _principal_covers_report_permissions(user: object, tenant: object, permissions: Sequence[str]) -> bool:
    """Evaluate all report permissions for one tenant under a bound principal."""
    try:
        tenant_id = getattr(tenant, "pk", None)
        user_id = getattr(user, "pk", None)
    # broad except: boundary-isolation: an unresolvable tenant or principal cannot authorize report data
    except Exception:
        return False
    if tenant_id is None or user_id is None:
        return False
    try:
        # inline import: app-registry: TaskContext is needed only for per-tenant report permission resolution
        from core.tasks.context import TaskContext

        with TaskContext(tenant_id=tenant_id, user_id=user_id, operation="reports.permissions"):
            bound_user = get_current_user()
            if (
                bound_user is None
                or not getattr(bound_user, "is_authenticated", False)
                or not getattr(bound_user, "is_active", False)
            ):
                return False
            return all(bound_user.has_perm(permission, obj=tenant) for permission in permissions)
    except (ObjectDoesNotExist, PermissionDenied):
        return False
    # broad except: boundary-isolation: principal and permission resolver errors must deny report compilation
    except Exception:
        return False


def _global_report_permissions_cover(
    user: object,
    permissions: Sequence[str],
    *,
    tenant_ids: Sequence[int] | None = None,
) -> bool:
    """Whether a principal holds every report permission on every live tenant.

    Global aggregation is fail-closed: tenant enumeration or permission-map
    failures, an empty tenant set, and expired or missing grants all deny.
    """
    try:
        if tenant_ids is None:
            tenants = tenant_model()._base_manager.filter(deleted_at__isnull=True)
            tenant_ids = tuple(tenants.values_list("pk", flat=True))
        if not tenant_ids:
            return False
        permission_map = build_accessible_tenant_permissions_map(user)
        if not isinstance(permission_map, Mapping):
            return False
        now = timezone.now()
        for tenant_id in tenant_ids:
            grant = permission_map.get(tenant_id)
            if not isinstance(grant, (tuple, list)) or len(grant) != 2:
                return False
            permissions_for_tenant, valid_until = grant
            if valid_until is not None and valid_until <= now:
                return False
            if not all(permission in permissions_for_tenant for permission in permissions):
                return False
        return True
    # broad except: boundary-isolation: global tenant enumeration and RBAC map errors must deny aggregation
    except Exception:
        return False


def _declared_report_permissions(provider: ReportDefinition) -> tuple[tuple[str, ...], bool]:
    """Read the provider's declared permissions; a broken or empty declaration is invalid."""
    try:
        permissions = tuple(provider.required_permissions())
    # broad except: boundary-isolation: a broken permission declaration cannot authorize report data
    except Exception:
        permissions = ()
    declaration_valid = bool(permissions) and all(
        isinstance(permission, str) and bool(str.strip(permission)) for permission in permissions
    )
    return permissions, declaration_valid


def _ambient_aggregate_scope_tenants(user: object) -> tuple[tuple[object, ...], bool]:
    """Resolve the ambient aggregate scope the scoped managers will read.

    Mirrors ``filter_by_tenant``'s aggregate resolution: the "all accessible
    tenants" scope is the canonical accessible set, and an active tenant-group
    scope is the accessible set intersected with the group's live subtree. An
    aggregate scope that resolves to no live tenant is unrepresentable (fail
    closed), never widened into a global view.
    """
    try:
        group = get_current_tenant_group()
        all_accessible = get_current_all_accessible()
        if group is None and not all_accessible:
            return (), False
        queryset = tenant_model()._base_manager.filter(
            pk__in=accessible_tenant_ids(user),
            deleted_at__isnull=True,
        )
        if group is not None:
            queryset = queryset.filter(
                group_id__in=get_descendant_tenant_group_ids(group.pk, live_only=True),
            )
        tenants = tuple(queryset)
    # broad except: boundary-isolation: an unresolvable aggregate scope cannot authorize report data
    except Exception:
        return (), False
    if not tenants:
        return (), False
    return tenants, True


def _effective_scope_tenants(
    filter_tenants: Sequence[object] | None,
    active_tenant: object | None,
    user: object | None,
) -> tuple[tuple[object, ...], bool]:
    """Resolve the effective compile scope exactly like the tenant managers.

    A pinned constellation is the scope; otherwise the active tenant; otherwise
    the ambient aggregate scope ("all accessible tenants" / tenant group),
    resolved the same way the scoped querysets are. Superusers and system
    contexts are genuinely unscoped (the global path downstream); an
    authenticated non-superuser whose context resolves no scope reads no rows
    in the scoped managers, so the scope is unrepresentable and compilation
    fails closed.
    """
    try:
        if filter_tenants:
            return tuple(filter_tenants), True
        if active_tenant is not None:
            return (active_tenant,), True
        if user is None or bool(getattr(user, "is_superuser", False)):
            return (), True
        if get_current_scope_conflict(user):
            return (), False
        ambient_tenant = get_current_tenant()
        if ambient_tenant is not None:
            return (ambient_tenant,), True
        return _ambient_aggregate_scope_tenants(user)
    # broad except: boundary-isolation: an unrepresentable effective scope cannot authorize report data
    except Exception:
        return (), False


def _resolve_ambient_report_principal() -> tuple[object | None, bool, bool, bool]:
    """Resolve the ambient principal as ``(user, resolved, active, superuser)``.

    An absent or unauthenticated principal resolves to ``None`` and takes the
    actorless system-authorization path; a resolution failure denies.
    """
    try:
        user = get_current_user()
    # broad except: boundary-isolation: unresolved ambient principals cannot authorize report data
    except Exception:
        return None, False, False, False
    if user is None or not bool(getattr(user, "is_authenticated", False)):
        return None, True, False, False
    return (
        user,
        True,
        bool(getattr(user, "is_active", False)),
        bool(getattr(user, "is_superuser", False)),
    )


def _actorless_report_authorization_is_valid(
    scope_tenants: Sequence[object],
    permissions: Sequence[str],
) -> bool:
    """Actorless runs compile only under explicit per-permission system authorization."""
    if len(scope_tenants) != 1:
        return False
    try:
        tenant_id = getattr(scope_tenants[0], "pk", None)
    # broad except: boundary-isolation: an unresolvable actorless tenant cannot authorize report data
    except Exception:
        tenant_id = None
    if tenant_id is None:
        return False
    try:
        return all(
            has_valid_system_authorization(
                tenant_id=tenant_id,
                permission=permission,
                operation=REPORT_COMPILATION_OPERATION,
            )
            for permission in permissions
        )
    # broad except: boundary-isolation: system authorization validation errors must deny
    except Exception:
        return False


def _principal_covers_every_scope_tenant(
    user: object,
    scope_tenants: Sequence[object],
    permissions: Sequence[str],
) -> bool:
    """Whether the principal holds every permission on every tenant in the scope."""
    try:
        return all(_principal_covers_report_permissions(user, tenant, permissions) for tenant in scope_tenants)
    # broad except: boundary-isolation: per-tenant resolver errors cannot authorize report data
    except Exception:
        return False


def _live_tenant_ids() -> tuple[int, ...]:
    """Enumerate every live tenant for global-aggregation coverage; errors deny."""
    try:
        return tuple(tenant_model()._base_manager.filter(deleted_at__isnull=True).values_list("pk", flat=True))
    # broad except: boundary-isolation: tenant enumeration errors must deny global reports
    except Exception:
        return ()


def _report_compilation_authorized(
    *,
    user: object | None,
    principal_resolved: bool,
    principal_active: bool,
    principal_superuser: bool,
    scope_tenants: Sequence[object],
    permissions: Sequence[str],
) -> tuple[bool, tuple[int, ...]]:
    """Decide authorization; live tenant ids are enumerated only for the truly global path."""
    if not principal_resolved:
        return False, ()
    if user is None:
        return _actorless_report_authorization_is_valid(scope_tenants, permissions), ()
    if not principal_active:
        return False, ()
    if principal_superuser:
        return True, ()
    if scope_tenants:
        return _principal_covers_every_scope_tenant(user, scope_tenants, permissions), ()
    live_ids = _live_tenant_ids()
    return _global_report_permissions_cover(user, permissions, tenant_ids=live_ids), live_ids


def _log_report_permission_denial(
    provider: ReportDefinition,
    permissions: Sequence[str],
    scope_tenants: Sequence[object],
    global_tenant_ids: Sequence[int],
    user: object | None,
) -> None:
    """Record the denial with only the identities that resolve safely."""
    try:
        tenant_ids = [getattr(tenant, "pk", None) for tenant in scope_tenants]
    # broad except: boundary-isolation: log only tenant identities that resolve safely
    except Exception:
        tenant_ids = []
    if not scope_tenants:
        tenant_ids = list(global_tenant_ids)
    try:
        principal_id = getattr(user, "pk", None)
    # broad except: boundary-isolation: log only an identity that resolves safely
    except Exception:
        principal_id = None
    try:
        report_type = getattr(provider, "report_type", None)
    # broad except: boundary-isolation: log only a report identifier that resolves safely
    except Exception:
        report_type = None
    logger.warning(
        "Report provider domain permission check denied compilation",
        extra={
            "operation": "reports.permissions",
            "report_type": report_type,
            "tenant_ids": tenant_ids,
            "permissions": [permission for permission in permissions if isinstance(permission, str)],
            "principal_id": principal_id,
        },
    )


def _enforce_report_provider_permissions(
    provider: ReportDefinition,
    filter_tenants: Sequence[object] | None,
    active_tenant: object | None,
) -> None:
    """Require every provider permission throughout the effective compile scope."""
    permissions, declaration_valid = _declared_report_permissions(provider)
    user, principal_resolved, principal_active, principal_superuser = _resolve_ambient_report_principal()
    scope_tenants, scope_resolution_valid = _effective_scope_tenants(filter_tenants, active_tenant, user)

    authorized = False
    global_tenant_ids: tuple[int, ...] = ()
    if declaration_valid and scope_resolution_valid:
        authorized, global_tenant_ids = _report_compilation_authorized(
            user=user,
            principal_resolved=principal_resolved,
            principal_active=principal_active,
            principal_superuser=principal_superuser,
            scope_tenants=scope_tenants,
            permissions=permissions,
        )
    if authorized:
        return
    _log_report_permission_denial(provider, permissions, scope_tenants, global_tenant_ids, user)
    raise ReportPermissionDenied(_REPORT_PERMISSION_DENIED_MESSAGE)


def _resolve_pinned_scope(
    active_tenant: object | None,
    filter_tenants: Sequence[object],
) -> Sequence[object]:
    """Qualify a pinned constellation before it compiles.

    A single pinned tenant is an ordinary single-tenant compilation: the
    acting user must be able to reach that tenant (their accessible set) or
    hold the cross-tenant reporting permission for it. A constellation that
    spans several tenants is an aggregation, so it requires the cross-tenant
    reporting permission for every pinned tenant -- the same boundary a broad
    scheduled report must pass. Without an acting user only a single pinned
    tenant compiles; everything broader fails closed instead of aggregating
    unsupervised.

    Every refusal raises ``ReportPermissionDenied`` (a ``PermissionError``) so
    scheduled runs record the distinct ``report.permission_denied`` outcome
    instead of a generic generation failure, while interactive surfaces keep
    their existing 403 mapping.
    """
    user = get_current_user()
    if user is not None and not getattr(user, "is_authenticated", True):
        # An unauthenticated principal has no tenant reach and no permissions;
        # treat it exactly like the no-user system path (fail closed).
        user = None
    tenant_ids = sorted({getattr(tenant, "pk", None) for tenant in filter_tenants})
    if None in tenant_ids:
        raise ReportPermissionDenied(_PINNED_TENANT_UNREACHABLE_MESSAGE)
    if user is None:
        active_id = getattr(active_tenant, "pk", None)
        if len(tenant_ids) == 1 and active_id in (None, tenant_ids[0]):
            return filter_tenants
        raise ReportPermissionDenied(_CROSS_TENANT_PERMISSION_MESSAGE)
    if getattr(user, "is_superuser", False):
        return filter_tenants
    if len(tenant_ids) > 1:
        for tenant in filter_tenants:
            if not _tenant_scope_reach_is_valid(user, tenant):
                raise ReportPermissionDenied(_CROSS_TENANT_PERMISSION_MESSAGE)
        return filter_tenants
    if tenant_ids[0] in accessible_tenant_ids(user) or _tenant_scope_reach_is_valid(user, filter_tenants[0]):
        return filter_tenants
    raise ReportPermissionDenied(_PINNED_TENANT_UNREACHABLE_MESSAGE)


def _resolve_report_scope(
    active_tenant: object | None,
    filter_tenants: Sequence[object] | None,
) -> Sequence[object] | None:
    """Apply the cross-tenant permission gate without domain imports.

    A persisted constellation is qualified at compile time: single pinned
    tenants only compile for callers that reach that tenant, multi-tenant
    constellations require the per-tenant cross-tenant reporting permission.
    An empty ``filter_tenants`` signals an aggregate compile under the ambient
    scope.  Without the permission, fall back to single-tenant when an active
    tenant is available, and refuse when neither tenant scope is.
    """
    if filter_tenants:
        return _resolve_pinned_scope(active_tenant, filter_tenants)

    user = get_current_user()
    if user is not None and user.has_perm("reports.view_cross_tenant_reports"):
        return filter_tenants
    if active_tenant is not None:
        return [active_tenant]
    raise PermissionError(_CROSS_TENANT_PERMISSION_MESSAGE)


def _group_rows(
    rows: Sequence[ReportRow],
    group_by_field: str | None,
) -> dict[str, list[ReportRow]]:
    grouped_data = {}
    if group_by_field:
        for row in rows:
            group_key = row.get(GROUP_FIELD, DEFAULT_GROUP)
            grouped_data.setdefault(group_key, []).append(row)
    else:
        grouped_data[DEFAULT_GROUP] = rows
    return grouped_data


def _report_specification_inputs(
    template: object,
    specification_filters: Sequence[object] | None,
    specification_definitions: Mapping[object, object] | None,
    specification_export_references: Sequence[object] | None,
) -> tuple[tuple[object, ...], Mapping[object, object], tuple[object, ...]]:
    """Resolve opaque domain DTOs without coupling core to a provider."""
    filters = specification_filters
    if filters is None:
        filters = getattr(template, "specification_filters", ()) or ()
    definitions = specification_definitions
    if definitions is None:
        definitions = getattr(template, "specification_definitions", {}) or {}
    references = specification_export_references
    if references is None:
        references = getattr(template, "specification_export_references", ()) or ()
    return tuple(filters), dict(definitions), tuple(references)


@contextmanager
def _report_compilation_scope(filter_tenants: Sequence[object]) -> Iterator[None]:
    """Compile inside exactly the authorized constellation.

    Every tenant-scoped manager truncates to the ambient request scope before
    the provider's explicit ``scope_to_tenants`` filter can run, so a
    constellation that differs from the ambient binding -- a pinned tenant
    other than the active one, or a multi-tenant aggregation -- must compile
    under an explicit scope or it would silently drop the other tenants' rows.
    A single pinned tenant narrows the ambient binding to that tenant; an
    aggregation suspends the ambient binding entirely and runs like a system
    read -- scoped managers treat "no scope + an authenticated principal" as
    a bug and fail closed to an empty queryset, so the principal is suspended
    alongside the binding, and the explicit authorized filter is the only
    boundary. The previous bindings are restored afterwards. An empty scope
    aggregates under the ambient binding unchanged.
    """
    if not filter_tenants:
        yield
        return
    # inline import: app-registry: the scope override is only needed while compiling.
    from core.context import get_current_user, override_current_tenant_scope, set_current_user

    if len(filter_tenants) == 1:
        with override_current_tenant_scope(filter_tenants[0]):
            yield
        return

    previous_user = get_current_user()
    with override_current_tenant_scope(None):
        set_current_user(None)
        try:
            yield
        finally:
            set_current_user(previous_user)


def build_report_context(
    template: object,
    active_tenant: object | None = None,
    filter_tenants: Sequence[object] | None = None,
    *,
    specification_filters: Sequence[object] | None = None,
    specification_definitions: Mapping[object, object] | None = None,
    specification_export_references: Sequence[object] | None = None,
) -> tuple[
    list[str],
    list[ReportRow],
    list[ReportSummary],
    dict[str, list[ReportRow]],
    str,
    dict[str, object],
]:
    """Build one report through the provider that owns its identifier.

    The return shape is the historical six-tuple because preview, download,
    scheduled reporting, and external integrations unpack it directly.
    """
    filter_tenants = _resolve_report_scope(active_tenant, filter_tenants)
    provider = get_report_provider(template.report_type)
    _enforce_report_provider_permissions(provider, filter_tenants, active_tenant)
    columns = provider.build_columns(template)
    (
        resolved_specification_filters,
        resolved_specification_definitions,
        resolved_specification_export_references,
    ) = _report_specification_inputs(
        template,
        specification_filters,
        specification_definitions,
        specification_export_references,
    )
    request = ReportRequest(
        template=template,
        active_tenant=active_tenant,
        filter_tenants=tuple(filter_tenants or ()),
        columns=tuple(columns),
        user=get_current_user(),
        as_of=timezone.now(),
        specification_filters=resolved_specification_filters,
        specification_definitions=resolved_specification_definitions,
        specification_export_references=resolved_specification_export_references,
    )
    # The ambient request binding would truncate the provider's scoped managers
    # to a subset of an authorized constellation before its explicit filter can
    # apply; this scope keeps the authorized constellation authoritative.
    with _report_compilation_scope(filter_tenants):
        result = provider.build(request)
    headers = headers_for(request.columns)
    grouped_data = _group_rows(result.rows, template.group_by_field)
    context_data = {
        "report_name": template.name,
        "description": template.description,
        "generated_at": request.as_of,
        "headers": headers,
        "grouped_data": grouped_data,
        "summary_cards": result.summary_cards,
        "distribution_chart": result.chart_svg,
        "specification_export": result.specification_export,
        "style_preset": template.style_preset,
        "is_compact": template.style_preset == "compact",
        "is_financial": template.style_preset == "financial",
        # Output-window disclosure: every surface states when it renders a
        # capped window or the sample instead of the full scope.
        "is_sample": result.is_sample,
        "row_limit": provider.row_limit,
        "truncated": result.truncated,
        "total_rows": result.total_rows,
        "disclosure_text": report_disclosure_text(
            result,
            provider.row_limit,
            getattr(provider, "truncated_disclosure", None),
        ),
    }
    return headers, result.rows, result.summary_cards, grouped_data, result.chart_svg, context_data
