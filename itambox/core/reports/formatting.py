"""Domain-blind currency formatting primitives and report adapters."""

from django.conf import settings
from django.utils import formats

_SYMBOL_AFTER = {
    "EUR": "€",
    "CHF": "CHF",
    "SEK": "kr",
    "NOK": "kr",
    "DKK": "kr",
}
_SYMBOL_BEFORE = {
    "USD": "$",
    "GBP": "£",
    "CAD": "CA$",
    "AUD": "A$",
    "JPY": "¥",
}


def resolve_currency_code(
    record_currency: str | None,
    tenant_currency: str | None,
    default_currency: str,
) -> str:
    """Resolve primitive currency values in record, tenant, default order."""
    for currency_code in (record_currency, tenant_currency, default_currency):
        normalized = (currency_code or "").strip()
        if normalized:
            return normalized.upper()
    return "EUR"


def format_money(value, *, currency_code: str) -> str:
    """Format a value with locale-aware separators and a currency marker."""
    formatted = formats.number_format(value, decimal_pos=2, use_l10n=True, force_grouping=True)
    if currency_code in _SYMBOL_AFTER:
        return f"{formatted}\xa0{_SYMBOL_AFTER[currency_code]}"
    if currency_code in _SYMBOL_BEFORE:
        return f"{_SYMBOL_BEFORE[currency_code]}{formatted}"
    return f"{formatted}\xa0{currency_code}"


def _record_currency(record_currency, active_tenant):
    """Resolve a money record's currency using record, tenant, then settings."""
    tenant_currency = getattr(active_tenant, "currency", None) if active_tenant is not None else None
    return resolve_currency_code(
        record_currency,
        tenant_currency,
        getattr(settings, "ITAMBOX_DEFAULT_CURRENCY", "EUR"),
    )


def _format_per_currency(amount_by_currency):
    """Render one money figure per currency without applying an unavailable FX rate."""
    items = sorted(amount_by_currency.items(), key=lambda kv: kv[1], reverse=True)
    if not items:
        default_code = resolve_currency_code(None, None, getattr(settings, "ITAMBOX_DEFAULT_CURRENCY", "EUR"))
        return format_money(0, currency_code=default_code)
    return " · ".join(
        format_money(
            amount,
            currency_code=resolve_currency_code(
                cur,
                None,
                getattr(settings, "ITAMBOX_DEFAULT_CURRENCY", "EUR"),
            ),
        )
        for cur, amount in items
    )


def _money(amount, currency_value, active_tenant):
    """Render a report cell in the record's resolved currency."""
    if amount is None:
        return "-"
    code = _record_currency(currency_value, active_tenant)
    return format_money(amount, currency_code=code)
