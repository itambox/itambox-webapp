"""Assembly of one report context from its domain provider.

This module knows nothing about any individual report: it resolves the tenant
scope, asks the registry for the provider that owns the identifier, hands it an
immutable request, and assembles the common context around the result it gets
back.  Every decision about *what* a report contains belongs to the provider in
the owning domain application.
"""

from collections.abc import Mapping, Sequence

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.utils import timezone

from core.reports.contracts import ReportRequest, ReportRow, ReportSummary
from core.tenant_scope import accessible_tenant_ids
from itambox.middleware import get_current_user

from .columns import headers_for
from .registry import get_report_provider
from .rendering import report_disclosure_text
from .rows import DEFAULT_GROUP, GROUP_FIELD

_CROSS_TENANT_PERMISSION_MESSAGE = (
    "Cross-tenant report aggregation requires the 'reports.view_cross_tenant_reports' permission."
)
_PINNED_TENANT_UNREACHABLE_MESSAGE = "This report is pinned to a tenant the acting user cannot access."


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
    """
    user = get_current_user()
    if user is not None and not getattr(user, "is_authenticated", True):
        # An unauthenticated principal has no tenant reach and no permissions;
        # treat it exactly like the no-user system path (fail closed).
        user = None
    tenant_ids = sorted({getattr(tenant, "pk", None) for tenant in filter_tenants})
    if None in tenant_ids:
        raise PermissionError(_PINNED_TENANT_UNREACHABLE_MESSAGE)
    if user is None:
        active_id = getattr(active_tenant, "pk", None)
        if len(tenant_ids) == 1 and active_id in (None, tenant_ids[0]):
            return filter_tenants
        raise PermissionError(_CROSS_TENANT_PERMISSION_MESSAGE)
    if getattr(user, "is_superuser", False):
        return filter_tenants
    if len(tenant_ids) > 1:
        for tenant in filter_tenants:
            if not _tenant_scope_reach_is_valid(user, tenant):
                raise PermissionError(_CROSS_TENANT_PERMISSION_MESSAGE)
        return filter_tenants
    if tenant_ids[0] in accessible_tenant_ids(user) or _tenant_scope_reach_is_valid(user, filter_tenants[0]):
        return filter_tenants
    raise PermissionError(_PINNED_TENANT_UNREACHABLE_MESSAGE)


def _resolve_report_scope(
    active_tenant: object | None,
    filter_tenants: Sequence[object] | None,
) -> Sequence[object] | None:
    """Apply the cross-tenant permission gate without domain imports.

    A persisted constellation is qualified at compile time: single pinned
    tenants only compile for callers that reach that tenant, multi-tenant
    constellations require the per-tenant cross-tenant reporting permission.
    An empty ``filter_tenants`` signals "global aggregation".  Without the
    permission, fall back to single-tenant when an active tenant is available,
    and refuse when neither tenant scope is.
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
