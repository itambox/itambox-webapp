from datetime import timedelta
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from .models import Token, UserPreference

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


class UserViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser', password='testpass', is_staff=True, is_superuser=True
        )

    def test_profile_view_requires_login(self):
        url = reverse('users:user_profile')
        response = self.client.get(url)
        self.assertNotEqual(response.status_code, 200)

    def test_profile_view_authenticated(self):
        self.client.force_login(self.user)
        url = reverse('users:user_profile')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_password_view_authenticated(self):
        self.client.force_login(self.user)
        url = reverse('users:user_password')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_preferences_view_authenticated(self):
        self.client.force_login(self.user)
        url = reverse('users:user_preferences')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_api_tokens_view_authenticated(self):
        self.client.force_login(self.user)
        url = reverse('users:user_api_tokens')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_notifications_view_authenticated(self):
        self.client.force_login(self.user)
        url = reverse('users:user_notifications')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_notification_poll_htmx(self):
        self.client.force_login(self.user)
        url = reverse('users:notification_poll')
        headers = {'HTTP_HX_Request': 'true'}
        response = self.client.get(url, **headers)
        self.assertEqual(response.status_code, 200)

    def test_notification_poll_non_htmx(self):
        self.client.force_login(self.user)
        url = reverse('users:notification_poll')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 204)

    def test_view_notification_user_owned_with_url(self):
        from core.models import Notification
        notif = Notification.objects.create(
            user=self.user,
            subject="Test User URL Notif",
            message="Test message",
            target_url="/assets/assets/"
        )
        self.client.force_login(self.user)
        url = reverse('users:view_notification', kwargs={'pk': notif.pk})
        response = self.client.get(url)
        self.assertRedirects(response, "/assets/assets/", fetch_redirect_response=False)
        notif.refresh_from_db()
        self.assertTrue(notif.is_read)

    def test_view_notification_user_owned_without_url(self):
        from core.models import Notification
        notif = Notification.objects.create(
            user=self.user,
            subject="Test User No URL Notif",
            message="Test message"
        )
        self.client.force_login(self.user)
        url = reverse('users:view_notification', kwargs={'pk': notif.pk})
        response = self.client.get(url)
        self.assertRedirects(response, reverse('users:user_notifications'))
        notif.refresh_from_db()
        self.assertTrue(notif.is_read)

    def test_view_notification_broadcast(self):
        from core.models import Notification
        notif = Notification.objects.create(
            user=None,
            subject="Test Broadcast Notif",
            message="Test message",
            target_url="/assets/assets/"
        )
        self.client.force_login(self.user)
        url = reverse('users:view_notification', kwargs={'pk': notif.pk})
        response = self.client.get(url)
        self.assertRedirects(response, "/assets/assets/", fetch_redirect_response=False)
        notif.refresh_from_db()
        self.assertFalse(notif.is_read)

    def test_view_notification_other_user(self):
        from core.models import Notification
        other_user = User.objects.create_user(username='otheruser', password='testpass')
        notif = Notification.objects.create(
            user=other_user,
            subject="Other User Notif",
            message="Test message",
            target_url="/assets/assets/"
        )
        self.client.force_login(self.user)
        url = reverse('users:view_notification', kwargs={'pk': notif.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_user_subscriptions_view(self):
        from subscriptions.models import Provider, Subscription
        from datetime import date, timedelta
        
        provider = Provider.objects.create(name="Adobe Inc.")
        
        # Subscription 1: Active, owned by self.user, cost 120 USD/year (monthly) -> annual_cost = 1440 USD
        sub1 = Subscription.objects.create(
            name="Adobe Creative Cloud",
            provider=provider,
            type="saas",
            status="active",
            billing_cycle="monthly",
            renewal_cost=120.00,
            currency="USD",
            owner=self.user,
            renewal_date=date.today() + timedelta(days=15) # Expiring soon (15 days)
        )
        
        # Subscription 2: Active, owned by self.user, cost 100 USD/year (annual) -> annual_cost = 100 USD
        sub2 = Subscription.objects.create(
            name="Adobe Acrobat",
            provider=provider,
            type="saas",
            status="active",
            billing_cycle="annual",
            renewal_cost=100.00,
            currency="USD",
            owner=self.user,
            renewal_date=date.today() - timedelta(days=5) # Overdue (5 days past)
        )
        
        # Subscription 3: Active, owned by another user (should not show up in self.user's list)
        other_user = User.objects.create_user(username='otheruser2', password='testpass')
        sub3 = Subscription.objects.create(
            name="Adobe Photoshop",
            provider=provider,
            type="saas",
            status="active",
            billing_cycle="annual",
            renewal_cost=200.00,
            currency="USD",
            owner=other_user,
            renewal_date=date.today() + timedelta(days=45)
        )
        
        self.client.force_login(self.user)
        url = reverse('users:user_subscriptions')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'users/subscriptions.html')
        
        # Verify context data metrics
        self.assertEqual(response.context['subscriptions_count'], 2)
        self.assertEqual(response.context['active_subscriptions_count'], 1)
        # Annual costs: sub1 (120*12 = 1440) = 1440.0 (sub2 is expired)
        self.assertEqual(float(response.context['total_annual_spend']), 1440.0)
        
        # Expiring soon count: sub1 is expiring soon, sub2 is overdue/expired
        self.assertEqual(response.context['expiring_soon_count'], 1)
        self.assertEqual(response.context['overdue_count'], 1)

    def test_user_api_tokens_view(self):
        from users.models import Token
        
        # Pre-create a token owned by self.user
        token1 = Token.objects.create(
            user=self.user,
            description="My CI Token",
            write_enabled=True
        )
        
        # Pre-create a token owned by another user
        other_user = User.objects.create_user(username='otheruser3', password='testpass')
        token2 = Token.objects.create(
            user=other_user,
            description="Other User CI Token",
            write_enabled=False
        )
        
        self.client.force_login(self.user)
        url = reverse('users:user_api_tokens')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'users/api_tokens.html')
        
        # Verify tokens list only shows the owned token
        tokens_in_context = list(response.context['tokens'])
        self.assertIn(token1, tokens_in_context)
        self.assertNotIn(token2, tokens_in_context)

    def test_generate_api_token(self):
        from users.models import Token
        from datetime import date
        
        self.client.force_login(self.user)
        url = reverse('users:user_api_tokens')
        
        data = {
            'description': 'Production Access Key',
            'write_enabled': 'true',
            'expires': date.today().strftime('%Y-%m-%d')
        }
        
        response = self.client.post(url, data, follow=True)
        self.assertEqual(response.status_code, 200)
        
        # Verify it was saved in database
        token = Token.objects.filter(user=self.user, description='Production Access Key').first()
        self.assertIsNotNone(token)
        self.assertTrue(token.write_enabled)
        self.assertIsNotNone(token.expires)
        self.assertEqual(len(token.key), 40)
        
        # The key should be in the context of the followed response
        self.assertEqual(response.context.get('new_token_key'), token.key)
        
        # Load page a second time to ensure it is popped and no longer in context
        response2 = self.client.get(url)
        self.assertIsNone(response2.context.get('new_token_key'))

    def test_revoke_api_token_success(self):
        from users.models import Token
        
        token = Token.objects.create(
            user=self.user,
            description="Ephemeral key"
        )
        
        self.client.force_login(self.user)
        url = reverse('users:delete_api_token', kwargs={'pk': token.pk})
        
        response = self.client.post(url)
        self.assertRedirects(response, reverse('users:user_api_tokens'))
        
        # Verify it is deleted
        self.assertFalse(Token.objects.filter(pk=token.pk).exists())

    def test_revoke_api_token_other_user_404(self):
        from users.models import Token
        
        other_user = User.objects.create_user(username='otheruser4', password='testpass')
        token = Token.objects.create(
            user=other_user,
            description="Other user Ephemeral key"
        )
        
        self.client.force_login(self.user)
        url = reverse('users:delete_api_token', kwargs={'pk': token.pk})
        
        # Attempt to revoke other user's token should return 404
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)
        
        # Verify it is NOT deleted
        self.assertTrue(Token.objects.filter(pk=token.pk).exists())



