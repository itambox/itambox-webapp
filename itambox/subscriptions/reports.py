"""Subscription-domain report providers."""

from django.utils.translation import gettext as _

from core.reports.charts import generate_bar_chart
from core.reports.contracts import ReportDefinition, ReportRequest
from core.reports.formatting import _format_per_currency, _money, _record_currency
from core.reports.registry import register_report_provider
from core.reports.rows import DEFAULT_GROUP
from subscriptions.models import Subscription

#: Billing cycle -> how many of that cycle make up a month's worth of spend.
#: A one-time charge is not recurring, so it contributes nothing to the
#: estimated monthly figure; an unrecognised cycle is treated as monthly.
_MONTHLY_DIVISORS = {
    "monthly": 1.0,
    "quarterly": 3.0,
    "biannual": 6.0,
    "annual": 12.0,
    "multi_year": 36.0,
}


def _monthly_cost(subscription):
    """The subscription's renewal cost amortized to a monthly equivalent."""
    cost = float(subscription.renewal_cost)
    if subscription.billing_cycle == "onetime":
        return 0.0
    return cost / _MONTHLY_DIVISORS.get(subscription.billing_cycle, 1.0)


def _monthly_spend(queryset, request: ReportRequest):
    """Monthly spend bucketed by currency, and again by (provider, currency).

    Subscriptions can carry differing ISO currencies and there is no FX source,
    so nothing here is ever summed across currencies: the cards render one
    figure per currency and the chart draws one bar per (provider, currency).
    """
    by_currency = {}
    by_provider = {}
    for subscription in queryset:
        if subscription.renewal_cost is None:
            continue
        monthly = _monthly_cost(subscription)
        currency = _record_currency(subscription.currency, subscription.tenant)
        by_currency[currency] = by_currency.get(currency, 0.0) + monthly
        provider_name = subscription.provider.name if subscription.provider else _("Generic")
        by_provider[(provider_name, currency)] = by_provider.get((provider_name, currency), 0.0) + monthly
    return by_currency, by_provider


def _spend_chart(by_provider, request: ReportRequest):
    """One bar per (provider, currency), ISO-qualified when currencies differ."""
    multi_currency = len({currency for _provider, currency in by_provider}) > 1
    chart_data = [
        {
            "label": f"{provider_name} ({currency})" if multi_currency else provider_name,
            "value": amount,
            "display": _money(amount, currency, request.active_tenant),
        }
        for (provider_name, currency), amount in by_provider.items()
    ]
    return generate_bar_chart(chart_data, title=_("Monthly Spend by Provider"))


class SubscriptionRenewalsReportProvider(ReportDefinition):
    """Subscription renewals: active subscriptions and their monthly spend."""

    report_type = "subscription_renewals"
    permission = "subscriptions.view_subscription"
    default_columns = ("subscription_name", "provider", "billing_cycle", "cost", "end_date")

    cells = {
        "subscription_name": lambda record, request: record.name or "-",
        "provider": lambda record, request: record.provider.name if record.provider else "-",
        "billing_cycle": lambda record, request: record.get_billing_cycle_display(),
        "cost": lambda record, request: _money(
            record.renewal_cost, getattr(record, "currency", None), request.active_tenant
        ),
        "end_date": lambda record, request: record.renewal_date.strftime("%Y-%m-%d") if record.renewal_date else "-",
    }

    sample_cells = {
        "subscription_name": "Office 365 E5",
        "provider": "Microsoft",
        "billing_cycle": "Monthly",
        "cost": "$1,200.00",
        "end_date": "2026-12-31",
    }

    group_resolvers = {
        "provider": lambda record, request: record.provider.name if record.provider else DEFAULT_GROUP,
    }

    sample_group_keys = {"provider": "Microsoft"}

    def get_queryset(self, request: ReportRequest):
        # 'tenant' is select_related because the currency of a blank-currency
        # subscription is resolved from its own tenant — without it that is N+1.
        queryset = Subscription.objects.filter(deleted_at__isnull=True, status="active").select_related(
            "provider", "tenant"
        )
        return self.scope_to_tenants(queryset, request)

    def build_rows(self, records, request: ReportRequest):
        return [self.row_for(record, request) for record in records]

    def build_summary(self, queryset, request: ReportRequest):
        if not request.template.include_summary_cards:
            return []
        by_currency, _by_provider = _monthly_spend(queryset, request)
        return [
            {"label": _("Active Subscriptions"), "value": str(queryset.count())},
            {"label": _("Est. Monthly Spend"), "value": _format_per_currency(by_currency)},
        ]

    def build_chart(self, queryset, records, request: ReportRequest):
        if not request.template.include_distribution_chart:
            return ""
        _by_currency, by_provider = _monthly_spend(queryset, request)
        return _spend_chart(by_provider, request)

    def build_sample_summary(self, request: ReportRequest):
        if not request.template.include_summary_cards:
            return []
        return [
            {"label": _("Active Subscriptions"), "value": "1 (Mock)"},
            {"label": _("Est. Monthly Spend"), "value": "$1,200.00"},
        ]

    def build_sample_chart(self, request: ReportRequest):
        if not request.template.include_distribution_chart:
            return ""
        sample_currency = _record_currency(None, None)
        return _spend_chart({("Microsoft", sample_currency): 1200.0}, request)


register_report_provider(SubscriptionRenewalsReportProvider())
