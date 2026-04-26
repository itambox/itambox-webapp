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
        Token.objects.create(user=self.user, description='Token 1')
        Token.objects.create(user=self.user, description='Token 2')
        keys = Token.objects.values_list('key', flat=True)
        self.assertEqual(len(set(keys)), 2)

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
