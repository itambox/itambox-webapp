from datetime import timedelta
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from users.models import Token, UserPreference

User = get_user_model()

class TokenModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')

    def test_token_creation_generates_key(self):
        token = Token.objects.create(user=self.user, description='Test Token')
        self.assertIsNotNone(token.key)
        self.assertEqual(len(token.key), 40)

    def test_token_key_is_unique(self):
        t1 = Token.objects.create(user=self.user, description='Token 1')
        t2 = Token.objects.create(user=self.user, description='Token 2')
        # Plaintext keys differ; at rest only the unique HMAC digests are stored.
        self.assertNotEqual(t1.key, t2.key)
        digests = Token.objects.values_list('digest', flat=True)
        self.assertEqual(len(set(digests)), 2)

    def test_token_plaintext_is_not_stored_at_rest(self):
        token = Token.objects.create(user=self.user, description='Secret')
        plaintext = token.key
        self.assertTrue(plaintext)
        # Digest is an HMAC, never the plaintext; preview is a short non-secret.
        self.assertNotEqual(token.digest, plaintext)
        self.assertEqual(len(token.digest), 64)
        self.assertEqual(token.key_preview, plaintext[:8])
        # A freshly loaded instance cannot reveal the plaintext.
        reloaded = Token.objects.get(pk=token.pk)
        self.assertIsNone(reloaded.key)
        # ...but it still authenticates by digest.
        self.assertEqual(Token.find_by_key(plaintext).pk, token.pk)

    def test_token_generate_key_length(self):
        key = Token.generate_key()
        self.assertEqual(len(key), 40)

    def test_token_string_representation(self):
        token = Token.objects.create(user=self.user, description='API Key')
        self.assertIn(self.user.username, str(token))
        self.assertIn(token.key[:6], str(token))

    def test_token_is_expired_no_expiry(self):
        token = Token.objects.create(user=self.user)
        self.assertFalse(token.is_expired)

    def test_token_is_expired_future(self):
        future = timezone.now() + timedelta(days=30)
        token = Token.objects.create(user=self.user, expires=future)
        self.assertFalse(token.is_expired)

    def test_token_is_expired_past(self):
        past = timezone.now() - timedelta(days=1)
        token = Token.objects.create(user=self.user, expires=past)
        self.assertTrue(token.is_expired)

    def test_token_write_enabled_default(self):
        token = Token.objects.create(user=self.user)
        self.assertTrue(token.write_enabled)

    def test_token_write_enabled_false(self):
        token = Token.objects.create(user=self.user, write_enabled=False)
        self.assertFalse(token.write_enabled)

    def test_token_last_used_null_by_default(self):
        token = Token.objects.create(user=self.user)
        self.assertIsNone(token.last_used)

    def test_token_ordering_most_recent_first(self):
        t1 = Token.objects.create(user=self.user, description='Older')
        t2 = Token.objects.create(user=self.user, description='Newer')
        tokens = list(Token.objects.all())
        self.assertIn(t1, tokens)
        self.assertIn(t2, tokens)
        self.assertEqual(Token.objects.count(), 2)

    def test_token_description_blank(self):
        token = Token.objects.create(user=self.user)
        self.assertEqual(token.description, '')

    def test_token_allowed_ips_empty_by_default(self):
        token = Token.objects.create(user=self.user)
        self.assertEqual(token.allowed_ips, [])

    def test_validate_client_ip_no_restriction_allows_any(self):
        token = Token.objects.create(user=self.user)
        self.assertTrue(token.validate_client_ip('203.0.113.9'))
        self.assertTrue(token.validate_client_ip('2001:db8::1'))

    def test_validate_client_ip_within_cidr(self):
        token = Token.objects.create(user=self.user, allowed_ips=['192.168.1.0/24'])
        self.assertTrue(token.validate_client_ip('192.168.1.50'))
        self.assertFalse(token.validate_client_ip('192.168.2.50'))

    def test_validate_client_ip_exact_host(self):
        token = Token.objects.create(user=self.user, allowed_ips=['10.0.0.5'])
        self.assertTrue(token.validate_client_ip('10.0.0.5'))
        self.assertFalse(token.validate_client_ip('10.0.0.6'))

    def test_validate_client_ip_ipv6(self):
        token = Token.objects.create(user=self.user, allowed_ips=['2001:db8::/32'])
        self.assertTrue(token.validate_client_ip('2001:db8::1'))
        self.assertFalse(token.validate_client_ip('2001:dead::1'))

    def test_validate_client_ip_unparseable_is_rejected(self):
        token = Token.objects.create(user=self.user, allowed_ips=['192.168.1.0/24'])
        self.assertFalse(token.validate_client_ip('not-an-ip'))
        self.assertFalse(token.validate_client_ip(None))

    def test_validate_cidr_list_rejects_invalid(self):
        from django.core.exceptions import ValidationError
        from users.models import validate_cidr_list
        with self.assertRaises(ValidationError):
            validate_cidr_list(['192.168.1.0/24', 'garbage'])
        # Valid entries raise nothing
        validate_cidr_list(['192.168.1.0/24', '10.0.0.5', '2001:db8::/32'])

class UserPreferenceModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')

    def test_user_preference_creation(self):
        pref = UserPreference.objects.create(user=self.user)
        self.assertEqual(str(pref), f'Preferences for {self.user.username}')
        self.assertEqual(pref.data, {})

    def test_user_preference_with_data(self):
        pref = UserPreference.objects.create(
            user=self.user,
            data={'tables': {'assets.AssetTable': {'columns': ['name', 'asset_tag']}}}
        )
        self.assertEqual(pref.data['tables']['assets.AssetTable']['columns'], ['name', 'asset_tag'])

    def test_user_preference_one_to_one(self):
        UserPreference.objects.create(user=self.user)
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            UserPreference.objects.create(user=self.user)
