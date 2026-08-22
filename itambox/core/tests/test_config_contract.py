"""
Pure configuration-contract helper tests (issue #439).

Covers the secret-free validation helpers in ``core.config_contract``:

* ``validate_secret_key`` — full Django ``security.W009`` parity (length,
  distinct-character count, forbidden ``django-insecure-`` prefix);
* ``parse_api_token_peppers`` — unset/valid/malformed tri-state parsing with
  rotation-ID collision guards;
* ``parse_field_encryption_keys`` — unset/valid/malformed tri-state parsing of
  the comma-separated Fernet keyring;
* ``validate_db_password`` — the production "explicitly configured" contract.

No Django settings are loaded and no database is touched; the helpers are pure
stdlib + ``cryptography``.
"""

import json

import pytest
from cryptography.fernet import Fernet

from core.config_contract import (
    ConfigState,
    parse_api_token_peppers,
    parse_field_encryption_keys,
    validate_db_password,
    validate_secret_key,
)

# ---------------------------------------------------------------------------
# SECRET_KEY — Django security.W009 parity
# ---------------------------------------------------------------------------


class TestValidateSecretKey:
    """The production SECRET_KEY predicate must mirror Django's deployment check.

    Django 5.2's ``django.core.checks.security.base._check_secret_key`` accepts
    a key only when it has >= 50 characters, >= 5 distinct characters, and does
    not start with ``django-insecure-``. The predicate classifies every vector
    identically and identifies exactly one failed rule.
    """

    VALID_KEY = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"  # 62 chars, 62 distinct

    def test_missing_rejected(self):
        result = validate_secret_key(None)
        assert result.valid is False
        assert result.failed_rule == "missing"

    def test_empty_rejected(self):
        result = validate_secret_key("")
        assert result.valid is False
        assert result.failed_rule == "too_short"

    def test_current_development_fallback_rejected(self):
        """The base-settings dev fallback must be rejected in production."""
        fallback = "django-insecure-dev-only-change-me-in-production"
        result = validate_secret_key(fallback)
        assert result.valid is False
        assert result.failed_rule == "forbidden_prefix"

    def test_49_character_key_rejected(self):
        key = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLM"  # 49 chars, 38 distinct
        assert len(key) == 49
        result = validate_secret_key(key)
        assert result.valid is False
        assert result.failed_rule == "too_short"

    def test_50_plus_but_few_distinct_rejected(self):
        key = "abcd" * 14  # 56 chars, 4 distinct characters
        result = validate_secret_key(key)
        assert result.valid is False
        assert result.failed_rule == "too_few_distinct_chars"

    def test_forbidden_prefix_rejected_even_when_long(self):
        key = "django-insecure-" + "abcdefghij" * 5  # 66 chars, many distinct
        result = validate_secret_key(key)
        assert result.valid is False
        assert result.failed_rule == "forbidden_prefix"

    def test_valid_50_character_key_accepted(self):
        key = "abcdefghij" * 5  # exactly 50 chars, 10 distinct
        assert len(key) == 50
        assert validate_secret_key(key).valid is True

    def test_valid_long_key_accepted(self):
        assert validate_secret_key(self.VALID_KEY).valid is True

    def test_result_never_contains_key_material(self):
        key = "django-insecure-very-secret-material"
        for result in (validate_secret_key(key), validate_secret_key("x" * 49)):
            assert key not in str(result)
            assert "django-insecure" not in str(result)
            assert result.failed_rule in {
                "missing",
                "too_short",
                "too_few_distinct_chars",
                "forbidden_prefix",
            }

    @pytest.mark.parametrize(
        "key",
        [
            None,
            "",
            "django-insecure-dev-only-change-me-in-production",
            "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLM",  # 49 chars
            "abcd" * 14,  # 56 chars, 4 distinct
            "django-insecure-" + "abcdefghij" * 5,
        ],
    )
    def test_characterization_matches_django_w009(self, key):
        """Representative vectors must agree with Django 5.2's deployment check."""
        from django.core.checks.security.base import _check_secret_key

        assert validate_secret_key(key).valid == _check_secret_key(key or "")

    def test_valid_vector_matches_django_w009(self):
        from django.core.checks.security.base import _check_secret_key

        assert validate_secret_key(self.VALID_KEY).valid == _check_secret_key(self.VALID_KEY)


# ---------------------------------------------------------------------------
# API-token peppers — unset vs valid vs malformed
# ---------------------------------------------------------------------------


class TestParseApiTokenPeppers:
    """``ITAMBOX_API_TOKEN_PEPPERS`` must distinguish unset from malformed.

    Unset/blank keeps the SECRET_KEY-derived fallback (warned in prod);
    explicitly malformed values must never silently downgrade to ``{}``.
    """

    PEPPER = "p" * 50  # exactly the documented 50-character minimum

    def test_unset_and_blank(self):
        for raw in (None, "", "   "):
            result = parse_api_token_peppers(raw)
            assert result.state is ConfigState.UNSET
            assert result.peppers == {}
            assert result.error is None

    def test_valid_single_pepper(self):
        result = parse_api_token_peppers(json.dumps({"1": self.PEPPER}))
        assert result.state is ConfigState.VALID
        assert result.peppers == {1: self.PEPPER}
        assert result.error is None

    def test_valid_rotation_mapping(self):
        pepper2 = "q" * 50
        result = parse_api_token_peppers(json.dumps({"1": self.PEPPER, "2": pepper2}))
        assert result.state is ConfigState.VALID
        assert result.peppers == {1: self.PEPPER, 2: pepper2}

    def test_minimum_secret_length_boundary(self):
        assert parse_api_token_peppers(json.dumps({"1": self.PEPPER})).state is ConfigState.VALID
        assert parse_api_token_peppers(json.dumps({"1": self.PEPPER[:-1]})).state is ConfigState.MALFORMED

    def test_malformed_json(self):
        result = parse_api_token_peppers("{not-json")
        assert result.state is ConfigState.MALFORMED
        assert result.peppers == {}
        assert result.error is not None

    def test_wrong_json_type(self):
        for raw in ('["a"]', '"a-string"', "42", "null", "true"):
            result = parse_api_token_peppers(raw)
            assert result.state is ConfigState.MALFORMED, raw
            assert result.peppers == {}

    def test_empty_object_rejected_when_explicitly_supplied(self):
        result = parse_api_token_peppers("{}")
        assert result.state is ConfigState.MALFORMED
        assert "empty" in result.error

    def test_non_string_values_rejected(self):
        for raw in ('{"1": 123}', '{"1": null}', '{"1": true}', '{"1": ["x"]}'):
            result = parse_api_token_peppers(raw)
            assert result.state is ConfigState.MALFORMED, raw

    def test_empty_secret_rejected(self):
        assert parse_api_token_peppers('{"1": ""}').state is ConfigState.MALFORMED

    def test_non_numeric_id_rejected(self):
        assert parse_api_token_peppers(json.dumps({"one": self.PEPPER})).state is ConfigState.MALFORMED

    def test_non_positive_id_rejected(self):
        assert parse_api_token_peppers(json.dumps({"0": self.PEPPER})).state is ConfigState.MALFORMED
        assert parse_api_token_peppers(json.dumps({"-1": self.PEPPER})).state is ConfigState.MALFORMED

    def test_normalized_id_collision_rejected(self):
        """``"1"`` and ``"01"`` must not silently become the same integer ID."""
        result = parse_api_token_peppers(
            json.dumps({"1": self.PEPPER, "01": "qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"})
        )
        assert result.state is ConfigState.MALFORMED
        assert result.peppers == {}

    def test_non_canonical_id_rejected(self):
        assert parse_api_token_peppers(json.dumps({"01": self.PEPPER})).state is ConfigState.MALFORMED

    def test_duplicate_raw_ids_rejected(self):
        raw = '{"1": "' + self.PEPPER + '", "1": "qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"}'
        assert parse_api_token_peppers(raw).state is ConfigState.MALFORMED

    def test_error_never_contains_secret_material(self):
        # Keep the marker below the 50-character minimum so the JSON mapping
        # itself is malformed (too-short secret), not accidentally valid.
        # NOTE: the variable is deliberately NOT named "secret = ..." — that
        # assignment pattern trips the repository's gitleaks generic-api-key
        # rule on a synthetic marker (CI secrets gate).
        short_marker = "short-marker-439"
        for raw in (
            "{broken " + short_marker,
            json.dumps({"1": short_marker}),  # valid JSON, but too short and thus malformed
            json.dumps({"1": 123}),
        ):
            result = parse_api_token_peppers(raw)
            assert result.state is ConfigState.MALFORMED
            assert result.error is not None
            assert short_marker not in result.error
            assert short_marker not in str(result)


# ---------------------------------------------------------------------------
# Field-encryption keyring — unset vs valid vs malformed
# ---------------------------------------------------------------------------


class TestParseFieldEncryptionKeys:
    """``ITAMBOX_FIELD_ENCRYPTION_KEYS`` parsing must distinguish unset from malformed.

    Unset/blank keeps the SECRET_KEY-derived fallback (warned in prod); an
    explicitly supplied non-empty value that contains only separators or
    whitespace is malformed configuration, not an accidental fallback.
    """

    KEY1 = Fernet.generate_key().decode("ascii")
    KEY2 = Fernet.generate_key().decode("ascii")

    def test_unset(self):
        for raw in (None, ""):
            result = parse_field_encryption_keys(raw)
            assert result.state is ConfigState.UNSET
            assert result.keys == ()
            assert result.error is None

    def test_whitespace_or_separator_only_is_malformed(self):
        for raw in ("   ", ",,,", " , ", "  ,  ,  "):
            result = parse_field_encryption_keys(raw)
            assert result.state is ConfigState.MALFORMED, repr(raw)
            assert result.keys == ()

    def test_valid_single_key(self):
        result = parse_field_encryption_keys(self.KEY1)
        assert result.state is ConfigState.VALID
        assert result.keys == (self.KEY1,)

    def test_valid_multiple_keys_preserve_order(self):
        result = parse_field_encryption_keys(f"{self.KEY1},{self.KEY2}")
        assert result.state is ConfigState.VALID
        assert result.keys == (self.KEY1, self.KEY2)

    def test_entries_are_trimmed(self):
        result = parse_field_encryption_keys(f" {self.KEY1} , {self.KEY2} ")
        assert result.keys == (self.KEY1, self.KEY2)

    def test_invalid_key_at_first_position(self):
        result = parse_field_encryption_keys(f"not-a-fernet-key,{self.KEY1}")
        assert result.state is ConfigState.MALFORMED
        assert "index 1" in result.error
        assert result.keys == ()

    def test_invalid_key_at_later_position(self):
        result = parse_field_encryption_keys(f"{self.KEY1},not-a-fernet-key")
        assert result.state is ConfigState.MALFORMED
        assert "index 2" in result.error

    def test_invalid_key_at_every_position_reports_first(self):
        result = parse_field_encryption_keys("bad-one,bad-two")
        assert result.state is ConfigState.MALFORMED
        assert "index 1" in result.error

    def test_error_identifies_index_but_never_the_value(self):
        bad = "this-is-not-a-valid-fernet-key-material"
        result = parse_field_encryption_keys(bad)
        assert result.state is ConfigState.MALFORMED
        assert bad not in result.error
        assert bad not in str(result)


# ---------------------------------------------------------------------------
# Database password — the production "explicitly configured" contract
# ---------------------------------------------------------------------------


class TestValidateDbPassword:
    def test_missing_or_blank_rejected(self):
        for raw in (None, "", "   "):
            assert validate_db_password(raw) is False, repr(raw)

    def test_any_explicit_value_accepted(self):
        # "explicitly configured" is the contract — the literal value is not
        # banned (an external operator may deliberately use it).
        assert validate_db_password("itambox") is True
        assert validate_db_password("x") is True
        assert validate_db_password("correct-horse-battery-staple") is True
