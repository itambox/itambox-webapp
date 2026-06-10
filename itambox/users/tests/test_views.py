from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from users.models import Token

User = get_user_model()

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
        from extras.models import Bookmark
        from organization.models import Tenant
        from django.contrib.contenttypes.models import ContentType
        
        tenant = Tenant.objects.create(name="Adobe Inc.", slug="adobe-inc")
        ct = ContentType.objects.get_for_model(tenant)
        
        b1 = Bookmark.objects.create(user=self.user, model=ct, object_id=tenant.pk)
        
        other_user = User.objects.create_user(username='otheruser2', password='testpass')
        b2 = Bookmark.objects.create(user=other_user, model=ct, object_id=tenant.pk)
        
        self.client.force_login(self.user)
        url = reverse('users:user_subscriptions')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'users/subscriptions.html')
        
        self.assertEqual(response.context['bookmarked_count'], 1)
        bookmarked_ids = [item['id'] for item in response.context['bookmarked_items']]
        self.assertIn(b1.pk, bookmarked_ids)
        self.assertNotIn(b2.pk, bookmarked_ids)

    def test_user_api_tokens_view(self):
        token1 = Token.objects.create(
            user=self.user,
            description="My CI Token",
            write_enabled=True
        )
        
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
        
        tokens_in_context = list(response.context['tokens'])
        self.assertIn(token1, tokens_in_context)
        self.assertNotIn(token2, tokens_in_context)

    def test_generate_api_token(self):
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
        
        token = Token.objects.filter(user=self.user, description='Production Access Key').first()
        self.assertIsNotNone(token)
        self.assertTrue(token.write_enabled)
        self.assertIsNotNone(token.expires)
        self.assertEqual(len(token.key), 40)
        
        self.assertEqual(response.context.get('new_token_key'), token.key)
        
        response2 = self.client.get(url)
        self.assertIsNone(response2.context.get('new_token_key'))

    def test_revoke_api_token_success(self):
        token = Token.objects.create(
            user=self.user,
            description="Ephemeral key"
        )
        
        self.client.force_login(self.user)
        url = reverse('users:delete_api_token', kwargs={'pk': token.pk})
        
        response = self.client.post(url)
        self.assertRedirects(response, reverse('users:user_api_tokens'))
        
        self.assertFalse(Token.objects.filter(pk=token.pk).exists())

    def test_revoke_api_token_other_user_404(self):
        other_user = User.objects.create_user(username='otheruser4', password='testpass')
        token = Token.objects.create(
            user=other_user,
            description="Other user Ephemeral key"
        )
        
        self.client.force_login(self.user)
        url = reverse('users:delete_api_token', kwargs={'pk': token.pk})
        
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Token.objects.filter(pk=token.pk).exists())


class BookmarkAndNotificationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='bookmarkuser', password='testpass123', is_staff=True, is_superuser=True
        )
        from organization.models import Tenant
        self.tenant = Tenant.objects.create(name='Test Tenant', slug='test-tenant')
        
    def test_bookmark_toggle_view_add_and_remove(self):
        from extras.models import Bookmark
        from django.contrib.contenttypes.models import ContentType
        
        self.client.force_login(self.user)
        ct = ContentType.objects.get_for_model(self.tenant)
        url = reverse('users:bookmark_toggle', kwargs={'content_type_id': ct.pk, 'object_id': self.tenant.pk})
        
        response = self.client.post(url)
        self.assertRedirects(response, self.tenant.get_absolute_url())
        self.assertTrue(Bookmark.objects.filter(user=self.user, model=ct, object_id=self.tenant.pk).exists())
        
        response = self.client.post(url)
        self.assertRedirects(response, self.tenant.get_absolute_url())
        self.assertFalse(Bookmark.objects.filter(user=self.user, model=ct, object_id=self.tenant.pk).exists())

    def test_bookmark_toggle_view_htmx_add_and_remove(self):
        from extras.models import Bookmark
        from django.contrib.contenttypes.models import ContentType
        
        self.client.force_login(self.user)
        ct = ContentType.objects.get_for_model(self.tenant)
        url = reverse('users:bookmark_toggle', kwargs={'content_type_id': ct.pk, 'object_id': self.tenant.pk})
        headers = {'HTTP_HX_Request': 'true'}
        
        response = self.client.post(url, **headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'btn-warning', response.content)
        self.assertIn(b'hx-target="this"', response.content)
        self.assertIn(b'X-CSRFToken', response.content)
        self.assertTrue(Bookmark.objects.filter(user=self.user, model=ct, object_id=self.tenant.pk).exists())
        
        response = self.client.post(url, **headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'btn-outline-secondary', response.content)
        self.assertIn(b'hx-target="this"', response.content)
        self.assertIn(b'X-CSRFToken', response.content)
        self.assertFalse(Bookmark.objects.filter(user=self.user, model=ct, object_id=self.tenant.pk).exists())

    def test_bookmark_toggle_view_htmx_delete_from_subscriptions_list(self):
        from extras.models import Bookmark
        from django.contrib.contenttypes.models import ContentType
        
        ct = ContentType.objects.get_for_model(self.tenant)
        Bookmark.objects.create(user=self.user, model=ct, object_id=self.tenant.pk)
        
        self.client.force_login(self.user)
        url = reverse('users:bookmark_toggle', kwargs={'content_type_id': ct.pk, 'object_id': self.tenant.pk})
        
        headers = {
            'HTTP_HX_Request': 'true',
            'HTTP_REFERER': 'http://127.0.0.1:8000/user/subscriptions/'
        }
        response = self.client.post(url, **headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        self.assertFalse(Bookmark.objects.filter(user=self.user, model=ct, object_id=self.tenant.pk).exists())

    def test_bookmark_change_notification_triggers(self):
        from extras.models import Bookmark
        from core.models import Notification
        from django.contrib.contenttypes.models import ContentType
        
        ct = ContentType.objects.get_for_model(self.tenant)
        Bookmark.objects.create(user=self.user, model=ct, object_id=self.tenant.pk)
        
        Notification.objects.filter(user=self.user).delete()
        
        self.tenant.comments = "Updated tenant comment"
        self.tenant.save()
        
        notifications = Notification.objects.filter(user=self.user)
        self.assertEqual(notifications.count(), 1)
        notif = notifications.first()
        self.assertIn("Updated", notif.subject)
        self.assertIn("Test Tenant", notif.message)
        self.assertEqual(notif.target_url, self.tenant.get_absolute_url())
        
        notifications.delete()
        
        self.tenant.delete()
        
        notifications = Notification.objects.filter(user=self.user)
        self.assertEqual(notifications.count(), 1)
        notif_del = notifications.first()
        self.assertIn("Deleted", notif_del.subject)
        self.assertIn("Test Tenant", notif_del.message)
        self.assertIsNone(notif_del.target_url)
