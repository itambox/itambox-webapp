"""Branch coverage for the extras custom-field value validators."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from extras.customfields import (
    _canonical_decimal,
    _parse_decimal,
    _validate_boolean,
    _validate_date,
    _validate_hostname,
    _validate_integer,
    _validate_text,
    validate_custom_field_regex,
)


class _CF(SimpleNamespace):
    def __init__(self, **kwargs):
        defaults = dict(
            decimal_scale=2,
            minimum_value=None,
            maximum_value=None,
            text_max_length=None,
            regex=None,
            validation_rule=None,
            required=False,
        )
        defaults.update(kwargs)
        super().__init__(**defaults)


class DecimalValidationTests(SimpleTestCase):
    def test_parse_rejects_bools_and_none(self):
        for bad in (True, False, None):
            with self.assertRaises(ValidationError) as ctx:
                _parse_decimal(_CF(), bad)
            self.assertEqual(ctx.exception.code, "INVALID_TYPE")

    def test_parse_rejects_invalid_scale(self):
        for scale in (None, -1, 7):
            with self.assertRaises(ValidationError) as ctx:
                _parse_decimal(_CF(decimal_scale=scale), "1.5")
            self.assertEqual(ctx.exception.code, "INVALID_RANGE")

    def test_parse_rejects_exponents_and_plus_signs(self):
        for bad in ("1e3", "1E3", "+5"):
            with self.assertRaises(ValidationError) as ctx:
                _parse_decimal(_CF(), bad)
            self.assertEqual(ctx.exception.code, "INVALID_VALUE")

    def test_parse_rejects_non_canonical_grammar(self):
        for bad in ("1.234", "00.5", "01", "1,5", "-", "abc", "1."):
            with self.assertRaises(ValidationError) as ctx:
                _parse_decimal(_CF(), bad)
            self.assertEqual(ctx.exception.code, "INVALID_VALUE")

    def test_parse_rejects_too_many_integer_digits(self):
        with self.assertRaises(ValidationError) as ctx:
            _parse_decimal(_CF(), "1234567890123456789")
        self.assertEqual(ctx.exception.code, "INVALID_RANGE")

    def test_parse_rejects_negative_zero(self):
        with self.assertRaises(ValidationError) as ctx:
            _parse_decimal(_CF(), "-0")
        self.assertEqual(ctx.exception.code, "INVALID_VALUE")

    def test_parse_accepts_canonical_values(self):
        self.assertEqual(str(_parse_decimal(_CF(), "12.5")), "12.5")
        self.assertEqual(str(_parse_decimal(_CF(), "-3")), "-3")
        self.assertEqual(str(_parse_decimal(_CF(decimal_scale=0), "7")), "7")
        self.assertEqual(str(_parse_decimal(_CF(), "0")), "0")

    def test_canonical_enforces_minimum_and_maximum(self):
        with self.assertRaises(ValidationError) as ctx:
            _canonical_decimal(_CF(minimum_value=Decimal("5.00")), "4")
        self.assertEqual(ctx.exception.code, "INVALID_RANGE")
        with self.assertRaises(ValidationError) as ctx:
            _canonical_decimal(_CF(maximum_value=Decimal("5.00")), "6")
        self.assertEqual(ctx.exception.code, "INVALID_RANGE")
        self.assertEqual(
            _canonical_decimal(_CF(minimum_value=Decimal("5.00"), maximum_value=Decimal("6.00")), "5.5"), "5.50"
        )


class HostnameValidationTests(SimpleTestCase):
    def test_rejects_invalid_hostnames(self):
        for bad in (5, "", "a" * 254, "host.", "ho st", "-host.example", "host.-example"):
            self.assertFalse(_validate_hostname(bad))

    def test_accepts_valid_hostnames(self):
        for good in ("example.com", "h", "a-b.example.com", "host.example.com"):
            self.assertTrue(_validate_hostname(good))


class TextValidationTests(SimpleTestCase):
    def test_rejects_non_strings(self):
        with self.assertRaises(ValidationError) as ctx:
            _validate_text(_CF(), 5)
        self.assertEqual(ctx.exception.code, "INVALID_TYPE")

    def test_optional_empty_text_passes_through(self):
        self.assertEqual(_validate_text(_CF(), ""), "")

    def test_enforces_max_length(self):
        with self.assertRaises(ValidationError) as ctx:
            _validate_text(_CF(text_max_length=2), "abc")
        self.assertEqual(ctx.exception.code, "INVALID_RANGE")

    def test_enforces_regex(self):
        validate_custom_field_regex(r"^[a-z]+$")
        with self.assertRaises(ValidationError) as ctx:
            _validate_text(_CF(regex=r"^[a-z]+$"), "ABC")
        self.assertEqual(ctx.exception.code, "INVALID_VALUE")
        self.assertEqual(_validate_text(_CF(regex=r"^[a-z]+$"), "abc"), "abc")

    def test_enforces_hostname_rule(self):
        with self.assertRaises(ValidationError) as ctx:
            _validate_text(_CF(validation_rule="rfc1123_hostname"), "not a host")
        self.assertEqual(ctx.exception.code, "INVALID_VALUE")
        self.assertEqual(_validate_text(_CF(validation_rule="rfc1123_hostname"), "example.com"), "example.com")


class IntegerValidationTests(SimpleTestCase):
    def test_rejects_bools_and_non_integers(self):
        for bad in (True, 1.5, "3"):
            with self.assertRaises(ValidationError) as ctx:
                _validate_integer(_CF(), bad)
            self.assertEqual(ctx.exception.code, "INVALID_TYPE")

    def test_enforces_minimum_and_maximum(self):
        with self.assertRaises(ValidationError) as ctx:
            _validate_integer(_CF(minimum_value=Decimal("3")), 2)
        self.assertEqual(ctx.exception.code, "INVALID_RANGE")
        with self.assertRaises(ValidationError) as ctx:
            _validate_integer(_CF(maximum_value=Decimal("3")), 4)
        self.assertEqual(ctx.exception.code, "INVALID_RANGE")
        self.assertEqual(_validate_integer(_CF(minimum_value=Decimal("3"), maximum_value=Decimal("5")), 4), 4)


class DateValidationTests(SimpleTestCase):
    def test_accepts_date_objects_and_canonical_strings(self):
        self.assertEqual(_validate_date(_CF(), date(2026, 1, 2)), "2026-01-02")
        self.assertEqual(_validate_date(_CF(), "2026-01-02"), "2026-01-02")

    def test_rejects_non_strings_and_bad_dates(self):
        with self.assertRaises(ValidationError) as ctx:
            _validate_date(_CF(), 5)
        self.assertEqual(ctx.exception.code, "INVALID_TYPE")
        for bad in ("2026-13-01", "not-a-date", "2026-1-2", "2026-01-02T00:00:00"):
            with self.assertRaises(ValidationError) as ctx:
                _validate_date(_CF(), bad)
            self.assertEqual(ctx.exception.code, "INVALID_VALUE")


class BooleanValidationTests(SimpleTestCase):
    def test_rejects_non_booleans(self):
        for bad in (1, "true", None):
            with self.assertRaises(ValidationError) as ctx:
                _validate_boolean(_CF(), bad)
            self.assertEqual(ctx.exception.code, "INVALID_TYPE")

    def test_accepts_booleans(self):
        self.assertIs(_validate_boolean(_CF(), True), True)
        self.assertIs(_validate_boolean(_CF(), False), False)
