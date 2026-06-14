"""
{% load money %}

{{ value|money:context_object }}

Formats a Decimal/float value with the currency symbol of *context_object*'s tenant.
Resolution: obj.currency (per-record, if set) → obj.tenant.currency →
obj.asset.tenant.currency → ITAMBOX_DEFAULT_CURRENCY.

Symbol placement and number formatting follow the currency:
  EUR / CHF  →  1.234,56 €    (symbol after, locale-aware separators)
  USD / GBP  →  $1,234.56     (symbol before, locale-aware separators)
"""
from django import template
from django.conf import settings
from django.utils import formats

register = template.Library()

_SYMBOL_AFTER = {
    'EUR': '€',
    'CHF': 'CHF',
    'SEK': 'kr',
    'NOK': 'kr',
    'DKK': 'kr',
}
_SYMBOL_BEFORE = {
    'USD': '$',
    'GBP': '£',
    'CAD': 'CA$',
    'AUD': 'A$',
    'JPY': '¥',
}


def _get_currency(obj):
    if obj is None:
        return getattr(settings, 'ITAMBOX_DEFAULT_CURRENCY', 'EUR') or 'EUR'
    # Prefer an explicit per-record currency (core.currency.CurrencyField) when
    # the model carries one and it is set; blank falls back to the tenant.
    own = getattr(obj, 'currency', None)
    if own:
        return own
    tenant = getattr(obj, 'tenant', None)
    if tenant is None:
        asset = getattr(obj, 'asset', None)
        if asset is not None:
            tenant = getattr(asset, 'tenant', None)
    if tenant is not None:
        return getattr(tenant, 'currency', None) or getattr(settings, 'ITAMBOX_DEFAULT_CURRENCY', 'EUR')
    return getattr(settings, 'ITAMBOX_DEFAULT_CURRENCY', 'EUR') or 'EUR'


@register.filter
def money(value, obj=None):
    """Format *value* as a currency string using *obj*'s tenant currency."""
    if value is None:
        return ''
    try:
        currency = _get_currency(obj).upper()
    except Exception:
        currency = 'EUR'

    # Locale-aware number formatting (respects USE_L10N, USE_THOUSAND_SEPARATOR).
    formatted = formats.number_format(value, decimal_pos=2, use_l10n=True, force_grouping=True)

    if currency in _SYMBOL_AFTER:
        return f'{formatted}\xa0{_SYMBOL_AFTER[currency]}'
    if currency in _SYMBOL_BEFORE:
        return f'{_SYMBOL_BEFORE[currency]}{formatted}'
    # Unknown currency — use ISO code as suffix.
    return f'{formatted}\xa0{currency}'
