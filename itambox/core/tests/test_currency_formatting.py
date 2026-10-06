"""Issue #445 currency-boundary characterization at the immutable base.

The platform module exists at the base, but delegates formatting to the
``money`` template filter.  These tests intentionally freeze that adapter's
bytes until the primitive implementation is extracted into the platform.
"""

from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings
from django.utils import translation

from core.reports.formatting import _money, _record_currency
from extras.templatetags.money import money

EXPECTED = {
    "de": {
        "EUR": "1.234,50\xa0€",
        "CHF": "1.234,50\xa0CHF",
        "SEK": "1.234,50\xa0kr",
        "NOK": "1.234,50\xa0kr",
        "DKK": "1.234,50\xa0kr",
        "USD": "$1.234,50",
        "GBP": "£1.234,50",
        "CAD": "CA$1.234,50",
        "AUD": "A$1.234,50",
        "JPY": "¥1.234,50",
        "XYZ": "1.234,50\xa0XYZ",
    },
    "en": {
        "EUR": "1,234.50\xa0€",
        "CHF": "1,234.50\xa0CHF",
        "SEK": "1,234.50\xa0kr",
        "NOK": "1,234.50\xa0kr",
        "DKK": "1,234.50\xa0kr",
        "USD": "$1,234.50",
        "GBP": "£1,234.50",
        "CAD": "CA$1,234.50",
        "AUD": "A$1,234.50",
        "JPY": "¥1,234.50",
        "XYZ": "1,234.50\xa0XYZ",
    },
}


class Issue445MoneyBoundaryCharacterizationTests(SimpleTestCase):
    """PASS at base: freeze the presentation/report behavior byte-for-byte."""

    def test_supported_and_unknown_currency_bytes_by_locale(self):
        for language, expected_by_code in EXPECTED.items():
            with self.subTest(language=language), translation.override(language):
                for code, expected in expected_by_code.items():
                    with self.subTest(code=code):
                        actual = money(Decimal("1234.5"), SimpleNamespace(currency=code))
                        self.assertEqual(actual, expected)

    def test_none_contract_differs_for_template_and_report(self):
        self.assertEqual(money(None), "")
        self.assertEqual(_money(None, "EUR", None), "-")

    @override_settings(ITAMBOX_DEFAULT_CURRENCY="")
    def test_blank_default_falls_back_to_eur(self):
        self.assertEqual(_record_currency("", None), "EUR")
        self.assertEqual(money(Decimal("1"), None), "1.00\xa0€")

    @override_settings(ITAMBOX_DEFAULT_CURRENCY="cad")
    def test_blank_and_lowercase_codes_are_normalized(self):
        tenant = SimpleNamespace(currency="")
        self.assertEqual(_record_currency(" usd ".strip(), tenant), "USD")
        self.assertEqual(_record_currency("", tenant), "CAD")
        self.assertEqual(money(Decimal("1"), SimpleNamespace(currency="gbp")), "£1.00")

    @override_settings(ITAMBOX_DEFAULT_CURRENCY="AUD")
    def test_object_currency_precedence(self):
        tenant = SimpleNamespace(currency="CHF")
        asset_tenant = SimpleNamespace(currency="SEK")
        asset = SimpleNamespace(tenant=asset_tenant)
        self.assertEqual(money(Decimal("1"), SimpleNamespace(currency="USD", tenant=tenant)), "$1.00")
        self.assertEqual(money(Decimal("1"), SimpleNamespace(currency="", tenant=tenant)), "1.00\xa0CHF")
        self.assertEqual(money(Decimal("1"), SimpleNamespace(currency="", tenant=None, asset=asset)), "1.00\xa0kr")
        self.assertEqual(money(Decimal("1"), SimpleNamespace(currency="", tenant=None, asset=None)), "A$1.00")

    def test_grouping_two_decimals_and_non_breaking_space_are_exact(self):
        with translation.override("en"):
            rendered = money(Decimal("1234.5"), SimpleNamespace(currency="EUR"))
        self.assertEqual(rendered, "1,234.50\xa0€")
        self.assertIn("\xa0", rendered)
        self.assertNotIn(" ", rendered)

    def test_accessor_exception_falls_back_to_eur(self):
        class BrokenCurrency:
            @property
            def currency(self):
                raise RuntimeError("must remain private")

        self.assertEqual(money(Decimal("1"), BrokenCurrency()), "1.00\xa0€")
