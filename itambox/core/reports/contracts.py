"""Contracts shared by the report orchestration and the domain providers."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from django.db.models import Q

from .rows import group_key_for, report_row, sample_group_key_for, sample_report_row

PUBLIC_REPORT_TYPES = (
    "asset_summary",
    "license_utilization",
    "subscription_renewals",
    "asset_maintenance",
    "asset_depreciation",
    "software_inventory",
    "contract_renewals",
    "warranty_expiration",
    "asset_disposal_eol",
    "hardware_inventory",
    "custody_compliance",
)

#: Upper bound on the rows any one report renders.
ROW_LIMIT = 500


@dataclass(frozen=True)
class ReportRequest:
    """Immutable inputs for one report compilation.

    Providers must use these explicit values rather than ambient tenant or
    clock context.  ``filter_tenants`` and ``columns`` are tuples so a provider
    cannot accidentally mutate the scope used by another stage.
    """

    template: Any
    active_tenant: Any | None
    filter_tenants: tuple[Any, ...]
    columns: tuple[str, ...]
    user: Any | None
    as_of: datetime


@dataclass
class ReportResult:
    """Materialized domain output consumed by common grouping and rendering."""

    rows: list[Mapping[str, Any]] = field(default_factory=list)
    summary_cards: list[Mapping[str, Any]] = field(default_factory=list)
    chart_svg: str = ""
    #: True when the scope held no data and the report shows its sample instead.
    is_sample: bool = False

    def __post_init__(self):
        self.rows = list(self.rows)
        self.summary_cards = list(self.summary_cards)


class ReportDefinition:
    """Base contract implemented by a domain report provider.

    A provider owns one report identifier end to end: which records it reads,
    how a row and a summary card render, and what the report shows while its
    scope is still empty. Permission declarations are retained as domain
    metadata for audit and future authorization surfaces; ``tenant_field``
    and ``allow_global_tenant`` are the tenant policy :meth:`scope_to_tenants`
    applies, and ``cells`` is both a row's content and its column order.

    Providers are registered once and shared across threads, so they must stay
    stateless: everything one compilation needs travels in its
    :class:`ReportRequest`.
    """

    #: Stable identifier persisted on ``ReportTemplate.report_type``.
    report_type: str

    #: The model permission(s) this report's data belongs to,
    #: ``app_label.codename``. A multi-model provider may require more than one.
    permission: str | tuple[str, ...]

    #: Queryset path from the reported model to its owning tenant.
    tenant_field: str = "tenant"

    #: True for a shared-catalogue model whose null-tenant rows belong in every
    #: tenant's report; False keeps a null-tenant row out as a system artifact.
    allow_global_tenant: bool = False

    #: Columns used when the template selects none.
    default_columns: tuple[str, ...] = ()

    #: Upper bound on the rows this report renders.
    row_limit: int = ROW_LIMIT

    #: Column key -> ``renderer(record, request)``, in row order.
    cells: Mapping[str, Callable] = {}

    #: Column key -> the value the sample row shows for it.
    sample_cells: Mapping[str, str] = {}

    #: ``group_by_field`` -> ``resolver(record, request)`` for a row's group key.
    group_resolvers: Mapping[str, Callable] = {}

    #: ``group_by_field`` -> the grouping key the sample row carries.
    sample_group_keys: Mapping[str, str] = {}

    # -- metadata-driven helpers ------------------------------------------

    def build_columns(self, template) -> tuple[str, ...]:
        return tuple(template.included_columns or self.default_columns)

    def required_permissions(self) -> tuple[str, ...]:
        """Return the permissions declared by this provider's metadata."""
        if isinstance(self.permission, str):
            return (self.permission,)
        return tuple(self.permission)

    def scope_to_tenants(self, queryset, request: ReportRequest, tenant_field=None):
        """Restrict a queryset to the report's tenant scope.

        ``filter_tenants`` wins when the template pins a constellation,
        otherwise the active tenant does.  With neither, the caller has already
        passed the cross-tenant permission gate and the queryset stays global.
        """
        field_path = tenant_field or self.tenant_field
        if request.filter_tenants:
            criteria = Q(**{f"{field_path}__in": request.filter_tenants})
        elif request.active_tenant:
            criteria = Q(**{field_path: request.active_tenant})
        else:
            return queryset
        if self.allow_global_tenant:
            criteria |= Q(**{f"{field_path}__isnull": True})
        return queryset.filter(criteria)

    def row_for(self, record, request: ReportRequest, cells=None):
        """One rendered row, keyed by header label and grouped by this provider."""
        return report_row(
            self.cells if cells is None else cells,
            request.columns,
            record,
            request,
            self.group_key(record, request),
        )

    def group_key(self, record, request: ReportRequest):
        return group_key_for(request.template.group_by_field, self.group_resolvers, record, request)

    def sample_row(self, request: ReportRequest):
        return sample_report_row(
            self.sample_cells,
            request.columns,
            sample_group_key_for(request.template.group_by_field, self.sample_group_keys),
        )

    # -- domain hooks ------------------------------------------------------

    def get_queryset(self, request: ReportRequest):
        """The tenant-scoped records this report reads."""
        raise NotImplementedError

    def build_rows(self, records, request: ReportRequest) -> Sequence[Mapping[str, Any]]:
        """Render the capped record window into report rows."""
        raise NotImplementedError

    def build_summary(self, queryset, request: ReportRequest) -> Sequence[Mapping[str, Any]]:
        """Summary cards for the whole scope, not just the rendered window."""
        raise NotImplementedError

    def build_chart(self, queryset, records, request: ReportRequest) -> str:
        """The distribution chart, from the whole scope or the rendered window."""
        return ""

    def build_sample_summary(self, request: ReportRequest) -> Sequence[Mapping[str, Any]]:
        return []

    def build_sample_chart(self, request: ReportRequest) -> str:
        return ""

    # -- orchestration -----------------------------------------------------

    def build(self, request: ReportRequest) -> ReportResult:
        """Compile the report, falling back to its sample when the scope is empty.

        Override only when one report spans several querysets; a provider that
        reads a single model never needs to.
        """
        queryset = self.get_queryset(request)
        records = list(queryset[: self.row_limit])
        rows = list(self.build_rows(records, request))
        if not rows:
            return self.build_sample(request)
        return ReportResult(
            rows=rows,
            summary_cards=list(self.build_summary(queryset, request)),
            chart_svg=self.build_chart(queryset, records, request),
        )

    def build_sample(self, request: ReportRequest) -> ReportResult:
        """The illustrative result the designer previews against empty data."""
        return ReportResult(
            rows=[self.sample_row(request)],
            summary_cards=list(self.build_sample_summary(request)),
            chart_svg=self.build_sample_chart(request),
            is_sample=True,
        )
