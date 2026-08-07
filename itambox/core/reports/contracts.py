"""Contracts shared by the report compiler and domain providers."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

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
    headers: list[str] | None = None
    is_sample: bool = False

    def __post_init__(self):
        self.rows = list(self.rows)
        self.summary_cards = list(self.summary_cards)


class ReportDefinition:
    """Base contract implemented by a domain report provider.

    The four domain hooks are intentionally small.  A provider may use the
    default ``build`` orchestration or override it when a report needs several
    related querysets (for example a stock report spanning three models).
    """

    report_type: str
    default_columns: tuple[str, ...] = ()

    def build_columns(self, template) -> tuple[str, ...]:
        return tuple(template.included_columns or self.default_columns)

    def get_queryset(self, request: ReportRequest):
        raise NotImplementedError

    def build_rows(self, queryset, request: ReportRequest) -> Sequence[Mapping[str, Any]]:
        raise NotImplementedError

    def build_summary(self, queryset, request: ReportRequest) -> Sequence[Mapping[str, Any]]:
        raise NotImplementedError

    def build_chart(self, queryset, request: ReportRequest) -> str:
        return ""

    def build(self, request: ReportRequest) -> ReportResult:
        queryset = self.get_queryset(request)
        return ReportResult(
            rows=list(self.build_rows(queryset, request)),
            summary_cards=list(self.build_summary(queryset, request)),
            chart_svg=self.build_chart(queryset, request),
        )
