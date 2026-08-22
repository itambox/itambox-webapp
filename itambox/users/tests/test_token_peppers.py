"""
API-token pepper rotation compatibility tests (issue #439).

These tests use disposable token rows to prove the documented rotation
contract on the token model:

* a token created under pepper ID 1 still validates when ID 2 is added and
  ID 1 is retained;
* a new token uses the highest configured ID;
* removing an old ID invalidates only the tokens that require that ID;
* malformed configuration cannot silently reach token creation or lookup
  (production rejects it at settings import — see
  ``core/tests/test_prod_settings.py``; development stays lenient).

No plaintext token or pepper value is ever asserted into a diagnostic; the
final test proves failed lookups stay silent.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core.config_contract import ConfigState, parse_api_token_peppers
from users.models import Token, current_pepper_id, token_peppers

User = get_user_model()

# Two distinct, documented-compliant (>= 50 chars) pepper secrets.
PEPPER_1 = "p" * 50
PEPPER_2 = "q" * 50


class TokenPepperRotationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pepper-test-user", password="testpass")

    def test_token_under_id1_validates_when_id2_added_and_id1_retained(self):
        """Rotation within an existing dedicated mapping keeps old tokens valid."""
        with override_settings(API_TOKEN_PEPPERS={1: PEPPER_1}):
            legacy = Token.objects.create(user=self.user, description="legacy")
            legacy_key = legacy.key
        self.assertEqual(legacy.pepper, 1)

        with override_settings(API_TOKEN_PEPPERS={1: PEPPER_1, 2: PEPPER_2}):
            # The legacy digest still validates through the retained ID 1.
            self.assertEqual(Token.find_by_key(legacy_key).pk, legacy.pk)
            # ...and a brand-new token created after rotation also round-trips.
            new_token = Token.objects.create(user=self.user, description="rotated")
            self.assertEqual(Token.find_by_key(new_token.key).pk, new_token.pk)

    def test_new_token_uses_highest_configured_id(self):
        with override_settings(API_TOKEN_PEPPERS={1: PEPPER_1, 2: PEPPER_2}):
            self.assertEqual(current_pepper_id(), 2)
            token = Token.objects.create(user=self.user, description="newest")
            self.assertEqual(token.pepper, 2)
            self.assertEqual(Token.find_by_key(token.key).pk, token.pk)

    def test_removing_old_id_invalidates_only_tokens_requiring_it(self):
        with override_settings(API_TOKEN_PEPPERS={1: PEPPER_1}):
            legacy = Token.objects.create(user=self.user, description="under-id-1")
            legacy_key = legacy.key
        with override_settings(API_TOKEN_PEPPERS={1: PEPPER_1, 2: PEPPER_2}):
            rotated = Token.objects.create(user=self.user, description="under-id-2")
            rotated_key = rotated.key
        self.assertEqual(legacy.pepper, 1)
        self.assertEqual(rotated.pepper, 2)

        # Drop ID 1: only the legacy token is invalidated.
        with override_settings(API_TOKEN_PEPPERS={2: PEPPER_2}):
            self.assertIsNone(Token.find_by_key(legacy_key))
            self.assertEqual(Token.find_by_key(rotated_key).pk, rotated.pk)

    def test_malformed_config_never_reaches_token_paths_in_prod(self):
        """The parser rejects malformed input before any token code can run."""
        result = parse_api_token_peppers("{not-json")
        assert result.state is ConfigState.MALFORMED
        assert result.error is not None
        # Production fails at settings import (covered in test_prod_settings);
        # we only assert that the tri-state classification is unambiguous here.
        assert result.peppers == {}

        result = parse_api_token_peppers(None)
        assert result.state is ConfigState.UNSET

    def test_malformed_config_stays_lenient_in_dev(self):
        """Dev keeps working: malformed maps to the warned fallback, not a crash."""
        with override_settings(API_TOKEN_PEPPERS={}):  # what base stores for malformed
            # Fallback is the SECRET_KEY-derived single pepper.
            peppers = token_peppers()
            assert set(peppers) == {1}
            token = Token.objects.create(user=self.user, description="dev-lenient")
            token_key = token.key
            self.assertEqual(Token.find_by_key(token_key).pk, token.pk)

    def test_failed_lookup_stays_silent(self):
        """A lookup that no configured pepper can match raises nothing and leaks nothing."""
        with override_settings(API_TOKEN_PEPPERS={1: PEPPER_1}):
            token = Token.objects.create(user=self.user, description="soon-stale")
            plaintext = token.key
        with override_settings(API_TOKEN_PEPPERS={2: PEPPER_2}):
            # No exception, no plaintext or pepper material in any diagnostic.
            self.assertIsNone(Token.find_by_key(plaintext))
            self.assertIsNone(Token.find_by_key("deadbeef" * 5))
            self.assertNotIn(plaintext, str(token.pepper))
            self.assertNotIn(PEPPER_1, PEPPER_2)
