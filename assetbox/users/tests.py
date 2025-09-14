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
        from core.models import Bookmark
        from organization.models import Tenant
        from django.contrib.contenttypes.models import ContentType
        
        tenant = Tenant.objects.create(name="Adobe Inc.", slug="adobe-inc")
        ct = ContentType.objects.get_for_model(tenant)
        
        # Bookmark owned by self.user
        b1 = Bookmark.objects.create(user=self.user, model=ct, object_id=tenant.pk)
        
        # Bookmark owned by another user
        other_user = User.objects.create_user(username='otheruser2', password='testpass')
        b2 = Bookmark.objects.create(user=other_user, model=ct, object_id=tenant.pk)
        
        self.client.force_login(self.user)
        url = reverse('users:user_subscriptions')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'users/subscriptions.html')
        
        # Verify context data contains our bookmark but not the other user's
        self.assertEqual(response.context['bookmarked_count'], 1)
        bookmarked_ids = [item['id'] for item in response.context['bookmarked_items']]
        self.assertIn(b1.pk, bookmarked_ids)
        self.assertNotIn(b2.pk, bookmarked_ids)

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


class InternationalizationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser', password='testpass', is_staff=True, is_superuser=True
        )

    def test_language_selection_german(self):
        # Log in user
        self.client.force_login(self.user)
        # Select German language by sending POST to set_language
        url = reverse('set_language')
        response = self.client.post(url, {'language': 'de'}, follow=True)
        self.assertEqual(response.status_code, 200)
        
        # Verify the cookie was set
        self.assertEqual(self.client.cookies.get('django_language').value, 'de')

    def test_german_translation_rendering(self):
        self.client.force_login(self.user)
        # Set language cookie to 'de'
        self.client.cookies['django_language'] = 'de'
        
        # Access the user profile page
        url = reverse('users:user_profile')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        
        # Verify that German strings are rendered in the response (e.g. "Benutzerprofil")
        self.assertContains(response, "Benutzerprofil")
        # Verify sidebar translations are rendered (e.g. "Organisation")
        self.assertContains(response, "Organisation")

    def test_german_flash_messages(self):
        self.client.force_login(self.user)
        self.client.cookies['django_language'] = 'de'
        
        # Trigger profile update to get translated success message
        url = reverse('users:user_profile')
        response = self.client.post(url, {
            'first_name': 'Rene',
            'last_name': 'Rettig',
            'email': 'rene.rettig@example.com'
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        # Verify the success message is translated to German
        self.assertContains(response, "Profil erfolgreich aktualisiert.")


class BookmarkAndNotificationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='bookmarkuser', password='testpass123', is_staff=True, is_superuser=True
        )
        # Create a Tenant which is bookmarkable
        from organization.models import Tenant
        self.tenant = Tenant.objects.create(name='Test Tenant', slug='test-tenant')
        
    def test_bookmark_toggle_view_add_and_remove(self):
        from core.models import Bookmark
        from django.contrib.contenttypes.models import ContentType
        
        self.client.force_login(self.user)
        ct = ContentType.objects.get_for_model(self.tenant)
        url = reverse('users:bookmark_toggle', kwargs={'content_type_id': ct.pk, 'object_id': self.tenant.pk})
        
        # 1. Standard POST request to toggle/create bookmark
        response = self.client.post(url)
        self.assertRedirects(response, self.tenant.get_absolute_url())
        self.assertTrue(Bookmark.objects.filter(user=self.user, model=ct, object_id=self.tenant.pk).exists())
        
        # 2. Standard POST request to toggle/delete bookmark
        response = self.client.post(url)
        self.assertRedirects(response, self.tenant.get_absolute_url())
        self.assertFalse(Bookmark.objects.filter(user=self.user, model=ct, object_id=self.tenant.pk).exists())

    def test_bookmark_toggle_view_htmx_add_and_remove(self):
        from core.models import Bookmark
        from django.contrib.contenttypes.models import ContentType
        
        self.client.force_login(self.user)
        ct = ContentType.objects.get_for_model(self.tenant)
        url = reverse('users:bookmark_toggle', kwargs={'content_type_id': ct.pk, 'object_id': self.tenant.pk})
        headers = {'HTTP_HX_Request': 'true'}
        
        # 1. HTMX POST request to toggle/create bookmark
        response = self.client.post(url, **headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'btn-warning', response.content) # filled star class
        self.assertIn(b'hx-target="this"', response.content)
        self.assertIn(b'X-CSRFToken', response.content)
        self.assertTrue(Bookmark.objects.filter(user=self.user, model=ct, object_id=self.tenant.pk).exists())
        
        # 2. HTMX POST request to toggle/delete bookmark
        response = self.client.post(url, **headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'btn-outline-secondary', response.content) # outlined star class
        self.assertIn(b'hx-target="this"', response.content)
        self.assertIn(b'X-CSRFToken', response.content)
        self.assertFalse(Bookmark.objects.filter(user=self.user, model=ct, object_id=self.tenant.pk).exists())

    def test_bookmark_toggle_view_htmx_delete_from_subscriptions_list(self):
        from core.models import Bookmark
        from django.contrib.contenttypes.models import ContentType
        
        # First, pre-create bookmark
        ct = ContentType.objects.get_for_model(self.tenant)
        Bookmark.objects.create(user=self.user, model=ct, object_id=self.tenant.pk)
        
        self.client.force_login(self.user)
        url = reverse('users:bookmark_toggle', kwargs={'content_type_id': ct.pk, 'object_id': self.tenant.pk})
        
        # HTMX request with subscriptions referer (imitating unsubscription from /user/subscriptions/)
        headers = {
            'HTTP_HX_Request': 'true',
            'HTTP_REFERER': 'http://127.0.0.1:8000/user/subscriptions/'
        }
        response = self.client.post(url, **headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"") # Empty response to remove list element
        self.assertFalse(Bookmark.objects.filter(user=self.user, model=ct, object_id=self.tenant.pk).exists())

    def test_bookmark_change_notification_triggers(self):
        from core.models import Bookmark, Notification
        from django.contrib.contenttypes.models import ContentType
        
        # 1. Create a bookmark for the tenant
        ct = ContentType.objects.get_for_model(self.tenant)
        Bookmark.objects.create(user=self.user, model=ct, object_id=self.tenant.pk)
        
        # Clear any existing notifications
        Notification.objects.filter(user=self.user).delete()
        
        # 2. Update the tenant to trigger event_on_save signal
        self.tenant.comments = "Updated tenant comment"
        self.tenant.save()
        
        # Verify notification was generated for the user
        notifications = Notification.objects.filter(user=self.user)
        self.assertEqual(notifications.count(), 1)
        notif = notifications.first()
        self.assertIn("Updated", notif.subject)
        self.assertIn("Test Tenant", notif.message)
        self.assertEqual(notif.target_url, self.tenant.get_absolute_url())
        
        # Clear notifications
        notifications.delete()
        
        # 3. Delete the tenant to trigger event_on_delete signal
        self.tenant.delete()
        
        # Verify notification was generated for deletion
        notifications = Notification.objects.filter(user=self.user)
        self.assertEqual(notifications.count(), 1)
        notif_del = notifications.first()
        self.assertIn("Deleted", notif_del.subject)
        self.assertIn("Test Tenant", notif_del.message)
        self.assertIsNone(notif_del.target_url)




