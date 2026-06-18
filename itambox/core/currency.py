"""Shared ISO 4217 currency support for money-bearing models.

Money columns in ITAMbox are bare ``DecimalField``s; this module adds the
*currency* dimension without pulling in a money library. A model records the
currency an amount is expressed in via::

    from core.currency import CurrencyField
    currency = CurrencyField()

Semantics: a blank value means "use the owning tenant's currency" (resolved at
display time by the ``{{ value|money:obj }}`` template filter in
``extras/templatetags/money.py``); a non-blank value overrides it — e.g. an EU
subsidiary recording a USD purchase. This keeps existing rows (blank) rendering
in the tenant currency while allowing explicit per-record currencies.

The set mirrors the currencies the ``money`` filter knows how to format; keep
the two in sync when adding entries.
"""
from django.db import models
from django.utils.translation import gettext_lazy as _

# (ISO 4217 code, human label). Order = display order in form dropdowns.
CURRENCY_CHOICES = [
    ('EUR', _('Euro (€)')),
    ('USD', _('US Dollar ($)')),
    ('GBP', _('British Pound (£)')),
    ('CHF', _('Swiss Franc (CHF)')),
    ('SEK', _('Swedish Krona (kr)')),
    ('NOK', _('Norwegian Krone (kr)')),
    ('DKK', _('Danish Krone (kr)')),
    ('CAD', _('Canadian Dollar (CA$)')),
    ('AUD', _('Australian Dollar (A$)')),
    ('JPY', _('Japanese Yen (¥)')),
]

CURRENCY_CODES = frozenset(code for code, _label in CURRENCY_CHOICES)


def CurrencyField(**kwargs):
    """A standardised currency column for money-bearing models.

    Returns a plain ``CharField`` (so migrations serialise normally). Blank =
    fall back to the tenant currency at display time; non-blank overrides it.
    """
    kwargs.setdefault('max_length', 3)
    kwargs.setdefault('blank', True)
    kwargs.setdefault('default', '')
    kwargs.setdefault('choices', CURRENCY_CHOICES)
    kwargs.setdefault('verbose_name', _('Currency'))
    kwargs.setdefault(
        'help_text',
        _('ISO 4217 code. Leave blank to use the tenant default currency.'),
    )
    return models.CharField(**kwargs)
