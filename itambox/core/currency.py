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

# (ISO 4217 code, human label). Order = display order in form dropdowns.
CURRENCY_CHOICES = [
    ('EUR', 'Euro (€)'),
    ('USD', 'US Dollar ($)'),
    ('GBP', 'British Pound (£)'),
    ('CHF', 'Swiss Franc (CHF)'),
    ('SEK', 'Swedish Krona (kr)'),
    ('NOK', 'Norwegian Krone (kr)'),
    ('DKK', 'Danish Krone (kr)'),
    ('CAD', 'Canadian Dollar (CA$)'),
    ('AUD', 'Australian Dollar (A$)'),
    ('JPY', 'Japanese Yen (¥)'),
]

CURRENCY_CODES = frozenset(code for code, _ in CURRENCY_CHOICES)


def CurrencyField(**kwargs):
    """A standardised currency column for money-bearing models.

    Returns a plain ``CharField`` (so migrations serialise normally). Blank =
    fall back to the tenant currency at display time; non-blank overrides it.
    """
    kwargs.setdefault('max_length', 3)
    kwargs.setdefault('blank', True)
    kwargs.setdefault('default', '')
    kwargs.setdefault('choices', CURRENCY_CHOICES)
    kwargs.setdefault('verbose_name', 'Currency')
    kwargs.setdefault(
        'help_text',
        'ISO 4217 code. Leave blank to use the tenant default currency.',
    )
    return models.CharField(**kwargs)
