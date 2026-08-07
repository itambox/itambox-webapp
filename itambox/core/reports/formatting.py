"""Shared currency formatting for report providers."""

from types import SimpleNamespace

from django.conf import settings


def _record_currency(record_currency, active_tenant):
    """Resolve a money record's currency using record, tenant, then settings."""
    code = (record_currency or "").upper()
    if code:
        return code
    if active_tenant is not None and getattr(active_tenant, "currency", None):
        return active_tenant.currency.upper()
    return (getattr(settings, "ITAMBOX_DEFAULT_CURRENCY", "EUR") or "EUR").upper()


def _format_per_currency(amount_by_currency):
    """Render one money figure per currency without applying an unavailable FX rate."""
    # inline import: heavy-import: load the presentation formatter only for financial reports
    from extras.templatetags.money import money as _money_fmt

    items = sorted(amount_by_currency.items(), key=lambda kv: kv[1], reverse=True)
    return " · ".join(_money_fmt(amount, SimpleNamespace(currency=cur)) for cur, amount in items) or _money_fmt(0, None)


def _money(amount, currency_value, active_tenant):
    """Render a report cell in the record's resolved currency."""
    if amount is None:
        return "-"

    # inline import: heavy-import: load the presentation formatter only for financial reports
    from extras.templatetags.money import money as _money_fmt

    code = _record_currency(currency_value, active_tenant)
    return _money_fmt(amount, SimpleNamespace(currency=code))
